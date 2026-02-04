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

from typing import List
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from sqlmodel import Session

from flip_api.auth.auth_utils import has_permissions
from flip.auth.dependencies import verify_token
from flip.db.database import get_session
from flip.db.models.main_models import Projects
from flip.db.models.user_models import PermissionRef
from flip.domain.interfaces.project import IProjectApproval
from flip.domain.interfaces.trust import ITrust
from flip.domain.schemas.projects import ApproveProjectBodyPayload
from flip.domain.schemas.status import ProjectStatus
from flip.project_services.services.project_services import approve_project
from flip.trusts_services.services.trust import get_trusts
from flip.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])


# TODO [#114] This endpoint was not defined in the old repo. It was used as a step of a 'approveProject' step function.
@router.post(
    "/{project_id}/approve",
    summary="Approve a staged project for specified trusts.",
    response_model=List[ITrust],
    status_code=status.HTTP_200_OK,
)
def approve_project_endpoint(
    project_id: UUID = Path(..., description="The ID of the project to approve."),
    payload: ApproveProjectBodyPayload = Body(
        ..., description="Payload containing trust IDs to approve the project for."
    ),
    user_id: UUID = Depends(verify_token),
    db: Session = Depends(get_session),
):
    """
    Approves a project that is currently in the 'STAGED' status.
    The approval is specific to the list of trust IDs provided in the request body.

    Args:
        project_id (UUID): The ID of the project to approve.
        payload (ApproveProjectBodyPayload): The payload containing trust IDs to approve the project for.
        user_id (UUID): The ID of the user making the request.
        db (Session): The database session.

    Returns:
        List[ITrust]: A list of trusts that the project has been approved for.

    Raises:
        HTTPException: If the user does not have permission to approve projects, if the project does not exist,
                       or if there are validation errors.
    """
    logger.debug(f"Attempting to approve project: {project_id} by user: {user_id}")

    # 1. Check user permissions
    if not has_permissions(user_id, [PermissionRef.CAN_APPROVE_PROJECTS], db):
        logger.error(f"User {user_id} does not have permission to approve project {project_id}.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User with ID: {user_id} was unable to approve this project",
        )

    # Schema validation
    trust_ids = payload.trusts

    project_approval = IProjectApproval(
        project_id=project_id,
        trust_ids=trust_ids,
    )

    # 2. Check if project exists
    project = db.get(Projects, project_id)
    if not project:
        logger.error(f"Project with ID {project_id} not found for approval.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project ID: {str(project_id)} does not exist",
        )

    # 3. Validate whether project has STAGED status
    if not project.status == ProjectStatus.STAGED:
        logger.error(f"Project {project_id} is not in STAGED status, cannot approve.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to approve the project as it has not been staged",
        )

    try:
        if not approve_project(db, project_approval, user_id):
            logger.error(f"Failed to approve project {project_id} for trusts: {trust_ids}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{trust_ids} is not a subset of the trusts selected during the staging process",
            )

        logger.debug(f"Fetching endpoints for trusts: {trust_ids} for project {project_id}")

        # Fetch trust endpoints based on the provided trust IDs
        trust_endpoints = get_trusts(db, ids=trust_ids)

        return trust_endpoints

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Unhandled error during project approval for {project_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
