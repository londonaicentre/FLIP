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
from flip_api.domain.interfaces.trust import (
    ICreateImagingProject,
    ITrust,
)
from flip_api.domain.schemas.status import ProjectStatus, TaskType
from flip_api.project_services.services.project_services import (
    get_approved_trusts_for_project,
    get_project,
    get_users_with_access,
)
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
        approved_trust_ids = {t.id for t in get_approved_trusts_for_project(project_id, db)}
        if project.status != ProjectStatus.APPROVED or trust.id not in approved_trust_ids:
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
