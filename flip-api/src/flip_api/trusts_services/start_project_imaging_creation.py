# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import json
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, status
from sqlmodel import Session

from flip_api.auth.dependencies import verify_token
from flip_api.auth.identity import IdentityProvider, get_identity_provider
from flip_api.auth.trust_authority import decision_maker_for
from flip_api.db.database import get_session
from flip_api.db.models.main_models import TrustTask
from flip_api.domain.interfaces.project import IProjectResponse
from flip_api.domain.interfaces.trust import (
    ICreateImagingProject,
    IPersistCohort,
    ITrust,
)
from flip_api.domain.schemas.status import ProjectStatus, TaskType
from flip_api.project_services.services.project_services import (
    get_approved_trusts_for_project,
    get_project,
    get_users_with_access,
)
from flip_api.utils.encryption import PROJECT_ID_CONTEXT, encrypt
from flip_api.utils.logger import logger

router = APIRouter(prefix="/trust", tags=["trusts_services"])


# TODO [#114] This endpoint was not defined in the old repo, rather it was run as a step in a step function
# 'approveProject' (Approves project and starts images creation on trusts).
@router.post(
    "/projects/{project_id}/trusts/imaging",
    summary="Start imaging project creation",
    description="Queues imaging project creation as a task for the trust to pick up via polling.",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=dict[str, str],
)
async def start_project_imaging_creation(
    request: Request,
    project_id: UUID = Path(..., description="ID of the project"),
    trust: ITrust = Body(..., description="Trust information"),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
    idp: IdentityProvider = Depends(get_identity_provider),
) -> dict[str, str]:
    """
    Queues imaging project creation as a task for the trust.

    Instead of making a direct HTTP call to the trust, this creates a TrustTask
    that the trust will pick up during its next polling cycle.

    Args:
        request (Request): FastAPI request object.
        project_id (UUID): ID of the project.
        trust (ITrust): Trust information.
        db (Session): Database session.
        user_id (UUID): User ID from the request context.
        idp (IdentityProvider): The identity provider, for the project users' directory records.

    Returns:
        dict[str, str]: Success message indicating the task has been queued.
    """
    # Permissions check — the same per-trust rule as the approval endpoint (FLIP#1258): a trust with a
    # Trust Admin is decided by them, one without by the hub admin. Checked against the trust named in the
    # body, so a caller cannot start imaging at a trust they could not have decided.
    if decision_maker_for(user_id, trust.id, db) is None:
        logger.error(f"User {user_id} may not start imaging creation for project {project_id} at trust {trust.id}")
        raise HTTPException(
            status_code=403,
            detail=f"User with ID: {user_id} was unable to start XNAT project creation",
        )

    return await queue_imaging_creation(request=request, project_id=project_id, trust=trust, db=db, idp=idp)


async def queue_imaging_creation(
    request: Request, project_id: UUID, trust: ITrust, db: Session, idp: IdentityProvider
) -> dict[str, str]:
    """
    Queues imaging project creation as a task for the trust, with no authority check of its own.

    The approval fan-out calls this directly rather than through the route above: it dispatches to every
    approved trust, including ones approved by an earlier call — possibly by another trust's owner — so the
    user completing the approval need hold no authority at those. Each trust's own recorded approval is what
    authorised its imaging, so it is refused (409) unless the project is approved and so is this trust.

    The trust's cohort snapshot (FLIP#857) is queued ahead of its imaging here, so every path that starts imaging at
    a trust — the approving call, a late trust's approval, the route above — has that trust freeze the cohort first.

    Args:
        request (Request): FastAPI request object.
        project_id (UUID): ID of the project.
        trust (ITrust): Trust information.
        db (Session): Database session.
        idp (IdentityProvider): The identity provider, for the project users' directory records. Passed in, not
            a ``Depends()`` default: this is a plain function, so FastAPI never resolves one here.

    Returns:
        dict[str, str]: Success message indicating the task has been queued.
    """
    try:
        # Get project details
        project = get_project(project_id, db)
        if not project:
            error_message = f"Central Hub project with {project_id=} not found. Unable to start XNAT project creation"
            logger.error(error_message)
            raise HTTPException(status_code=404, detail=error_message)

        # FLIP#1071: a project created without imaging has no imaging stage. Refuse here too, so a
        # direct call cannot create XNAT projects and queue pulls that the UI never shows (the
        # status route returns [] for such projects).
        if not project.has_imaging:
            raise HTTPException(
                status_code=409,
                detail=f"Project {project_id} was created without imaging; there is no imaging stage to start.",
            )

        # FLIP#1258: imaging pulls a trust's patients' studies, so it follows that trust's own approval — never a
        # project still awaiting decisions, nor a trust that declined or has not decided.
        if not _trust_has_approved(project_id, project, trust, db):
            raise HTTPException(
                status_code=409,
                detail=f"Trust {trust.name} has not approved project {project_id}; imaging cannot start there.",
            )

        # Get project users
        users_with_access = [uid for uid in get_users_with_access(project_id, db)]

        # Add owner of project to list of users
        users_with_access.append(project.owner_id)
        unique_users = {uid for uid in users_with_access}

        # Get the identity-provider records for them
        cognito_users = idp.list_users()

        # Freeze the approved cohort BEFORE imaging creation (FLIP#857): the frozen accession set must exist by the
        # time imaging retrieval asks for it. Queued and committed first: pending-task dispatch orders by created_at
        # and the trust poller processes sequentially, so the separate commit gives the snapshot task a strictly
        # earlier timestamp than the imaging task below.
        _queue_persist_cohort(project_id, project, trust, db)

        # Create request data for trust
        request_data = ICreateImagingProject(
            project_id=project_id,
            trust_id=trust.id,
            project_name=project.name,
            query=project.query.query if project.query else None,
            users=[user for user in cognito_users if user.id in unique_users],
            dicom_to_nifti=project.dicom_to_nifti,
        )

        # Queue task for trust (instead of direct HTTP call)
        task = TrustTask(
            trust_id=trust.id,
            task_type=TaskType.CREATE_IMAGING,
            payload=json.dumps(request_data.model_dump(mode="json"), default=str),
        )
        db.add(task)
        db.commit()

        logger.info(f"Queued imaging creation task for trust {trust.name}, project {project_id}")
        return {"success": "Imaging project creation task queued successfully"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"An error occurred while queuing project imaging creation: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


def queue_cohort_snapshot(project_id: UUID, trust: ITrust, db: Session) -> dict[str, str]:
    """
    Queues the approval-time cohort freeze (FLIP#857) at one trust, with no authority check of its own.

    The approval fan-out calls this in place of ``queue_imaging_creation`` for a project created without imaging
    (FLIP#1071). Such a project has no imaging stage, but its FL training still reads the cohort through
    data-access-api's ``/cohort/dataframe``, which serves only the members frozen at approval and refuses a project
    with no frozen membership — so each approved trust freezes the cohort all the same. Like imaging, it is refused
    (409) unless the project is approved and so is this trust.

    Args:
        project_id (UUID): ID of the project.
        trust (ITrust): Trust information.
        db (Session): Database session.

    Returns:
        dict[str, str]: Success message indicating the task has been queued.
    """
    try:
        project = get_project(project_id, db)
        if not project:
            error_message = f"Central Hub project with {project_id=} not found. Unable to freeze its cohort"
            logger.error(error_message)
            raise HTTPException(status_code=404, detail=error_message)

        if not _trust_has_approved(project_id, project, trust, db):
            raise HTTPException(
                status_code=409,
                detail=f"Trust {trust.name} has not approved project {project_id}; its cohort cannot be frozen there.",
            )

        _queue_persist_cohort(project_id, project, trust, db)
        return {"success": "Cohort snapshot task queued successfully"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"An error occurred while queuing the cohort snapshot: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


def _trust_has_approved(project_id: UUID, project: IProjectResponse, trust: ITrust, db: Session) -> bool:
    """Whether the project is approved and so is this trust — what starts anything at a trust (FLIP#1258)."""
    approved_trust_ids = {t.id for t in get_approved_trusts_for_project(project_id, db)}
    return project.status == ProjectStatus.APPROVED and trust.id in approved_trust_ids


def _queue_persist_cohort(project_id: UUID, project: IProjectResponse, trust: ITrust, db: Session) -> None:
    """
    Queues and commits the trust's PERSIST_COHORT task (FLIP#857).

    The trust records the approved cohort's membership once and its row-level routes then serve only those members,
    so every path that starts a trust on a project — its imaging, or a project without imaging — queues this first.

    Args:
        project_id (UUID): ID of the project.
        project (IProjectResponse): The project, carrying its query of record.
        trust (ITrust): Trust information.
        db (Session): Database session.

    Raises:
        HTTPException: 409 if the project has no cohort query — there is no cohort to freeze, and the trust would
                       refuse the project, so the caller's step fails for this trust rather than carrying on.
    """
    if project.query is None:
        logger.error(f"Project {project_id} has no cohort query; its cohort cannot be frozen at trust {trust.name}")
        raise HTTPException(
            status_code=409,
            detail=f"Project {project_id} has no cohort query; its cohort cannot be frozen at trust {trust.name}.",
        )

    persist_payload = IPersistCohort(
        project_id=project_id,
        trust_id=trust.id,
        encrypted_project_id=encrypt(str(project_id), context=PROJECT_ID_CONTEXT),
        query=project.query.query,
        query_id=project.query.id,
    )
    persist_task = TrustTask(
        trust_id=trust.id,
        task_type=TaskType.PERSIST_COHORT,
        # query_id on the task row records which Queries row was frozen (indexed).
        query_id=project.query.id,
        payload=json.dumps(persist_payload.model_dump(mode="json"), default=str),
    )
    db.add(persist_task)
    db.commit()
    logger.info(f"Queued cohort snapshot task for trust {trust.name}, project {project_id}")
