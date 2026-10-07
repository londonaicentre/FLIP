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

from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from sqlmodel import Session

from flip_api.auth.dependencies import verify_token
from flip_api.auth.trust_authority import decision_maker_for, trusts_with_admin
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Projects
from flip_api.domain.interfaces.project import IProjectApproval
from flip_api.domain.interfaces.trust import ITrust
from flip_api.domain.schemas.projects import ApproveProjectBodyPayload
from flip_api.domain.schemas.status import DecisionMaker, ProjectStatus
from flip_api.project_services.services.project_services import (
    InvalidTrustDecisionsError,
    ProjectNotStagedError,
    record_trust_decisions,
)
from flip_api.trusts_services.services.trust import get_trusts
from flip_api.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])


# TODO [#114] This endpoint was not defined in the old repo. It was used as a step of a 'approveProject' step function.
@router.post(
    "/{project_id}/approve",
    summary="Record trust decisions on a project; it is approved as soon as one trust approves.",
    response_model=list[ITrust],
    status_code=status.HTTP_200_OK,
)
def approve_project_endpoint(
    project_id: UUID = Path(..., description="The ID of the project to decide on."),
    payload: ApproveProjectBodyPayload = Body(
        ..., description="Payload containing the trust IDs to approve the project for and those that decline it."
    ),
    user_id: UUID = Depends(verify_token),
    db: Session = Depends(get_session),
) -> list[ITrust]:
    """
    Records trust decisions on a staged or approved project (FLIP#1258).
    The trusts in ``trusts`` approve the project and those in ``declined`` decline it; any trust named in neither
    keeps its current decision. The project is approved as soon as one trust approves; a trust still pending may be
    decided later, and joins then. If every trust declined it stays STAGED.

    Args:
        project_id (UUID): The ID of the project to decide on.
        payload (ApproveProjectBodyPayload): The trust IDs that approve the project and those that decline it.
        user_id (UUID): The ID of the user making the request.
        db (Session): The database session.

    Returns:
        list[ITrust]: The trusts this call starts — every approved trust on the call that approves the project, the
        newly approved ones on a later call — otherwise an empty list.

    Raises:
        HTTPException: 403 if the call names no trust, or a trust the caller may not decide (the trust's Trust
                       Admin decides a trust that has one, the hub admin one that does not); 404 if the project
                       does not exist; 400 on validation errors.
    """
    logger.debug(f"Attempting to approve project: {project_id} by user: {user_id}")

    # Every trust the call decides for, approving or declining: both are the site's decision.
    trust_ids = [*payload.trusts, *payload.declined]

    # 1. Check user permissions — per trust (FLIP#1258).
    #
    # A trust with a Trust Admin decides for itself: approving or declining there takes
    # CAN_APPROVE_FOR_TRUST at that trust, and the hub admin's global grant does not satisfy it.
    # A trust with none is decided by the hub admin (CAN_APPROVE_PROJECTS), as before site
    # approval existed. Each decision records which of the two made it.
    #
    # All-or-nothing: one trust the caller may not decide refuses the whole call. A partial
    # decision would be worse than a refusal — the caller gets a success for a request that
    # was only partly carried out, and some trusts are decided by someone with no authority
    # over them. `record_trust_decisions` commits the set in a single transaction for the same reason.
    #
    # The empty case is handled explicitly because this reads as "deny if any named trust is
    # unauthorised", and that is vacuously satisfied by an empty list — a request naming no
    # trusts would be authorised by anyone and would then decide nothing while reporting
    # success. Same fail-open shape as `has_permissions([])`; refused here for the same reason.
    if not trust_ids:
        logger.error(f"Approval of project {project_id} by user {user_id} named no trusts.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User with ID: {user_id} was unable to approve this project",
        )

    site_run = trusts_with_admin(trust_ids, db)
    decided_as: dict[UUID, DecisionMaker] = {}
    unauthorised = []
    for trust_id in trust_ids:
        maker = decision_maker_for(user_id, trust_id, db, has_admin=trust_id in site_run)
        if maker is None:
            unauthorised.append(trust_id)
        else:
            decided_as[trust_id] = maker
    if unauthorised:
        # The detail names no trust: which trusts a user lacks authority over is not the
        # caller's business, and echoing the list would let one probe the federation's
        # role assignments. The trusts go to the hub's log instead.
        logger.error(f"User {user_id} may not decide project {project_id} for trust(s) {unauthorised}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User with ID: {user_id} was unable to approve this project",
        )

    # Schema validation
    project_approval = IProjectApproval(
        project_id=project_id,
        trust_ids=payload.trusts,
        declined_trust_ids=payload.declined,
    )

    # 2. Check if project exists
    project = db.get(Projects, project_id)
    if not project:
        logger.error(f"Project with ID {project_id} not found for approval.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project ID: {str(project_id)} does not exist",
        )

    # 3. Validate that the project is open to decisions: STAGED, or APPROVED with trusts still to decide.
    if project.status == ProjectStatus.UNSTAGED:
        logger.error(f"Project {project_id} is not staged, cannot record trust decisions.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to approve the project as it has not been staged",
        )

    try:
        outcome = record_trust_decisions(db, project_approval, user_id, decided_as=decided_as)

        # get_trusts with no ids returns every trust, so an empty list must never reach it.
        if not outcome.activated_trust_ids:
            logger.info(f"Project {project_id} is {outcome.project_status}; these decisions start no trust")
            return []

        logger.debug(f"Fetching endpoints for activated trusts: {outcome.activated_trust_ids} for project {project_id}")
        return get_trusts(db, ids=outcome.activated_trust_ids)

    except ProjectNotStagedError:
        # The project was unstaged between the check above and taking the project lock.
        logger.error(f"Project {project_id} was unstaged before its trust decisions were recorded.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to approve the project as it has not been staged",
        )
    except InvalidTrustDecisionsError as e:
        logger.error(f"Rejected trust decisions on project {project_id}: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.exception(f"Unhandled error during project approval for {project_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e
