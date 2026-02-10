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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.interfaces.project import ProjectStatus
from flip_api.project_services.services.project_services import get_project, unstage_project_service
from flip_api.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])


# [#114] ✅
@router.post(
    "/{project_id}/unstage",
    summary="Unstage a project that is currently staged for approval.",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Project is not in STAGED status and cannot be unstaged."},
        status.HTTP_403_FORBIDDEN: {"description": "User does not have permission to unstage projects."},
        status.HTTP_404_NOT_FOUND: {"description": "Project not found."},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "An unexpected error occurred."},
    },
)
def unstage_project_endpoint(
    project_id: UUID,
    session: Session = Depends(get_session),
    current_user_id: UUID = Depends(verify_token),
) -> None:
    """
    Unstages a project, removing it from the approval process.
    The project must be in 'STAGED' status.
    User must have CAN_UNSTAGE_PROJECTS permission.

    Args:
        project_id (UUID): The ID of the project to unstage.
        session (Session): Database session.
        current_user_id (UUID): The ID of the current user.

    Returns:
        None

    Raises:
        HTTPException: Various exceptions for permission issues, not found, bad request, or server errors.
    """
    logger.info(f"User {current_user_id} attempting to unstage project {project_id}")

    # Check user permissions
    if not has_permissions(current_user_id, [PermissionRef.CAN_UNSTAGE_PROJECTS], session):
        logger.error(f"User with ID: {current_user_id} is not allowed to unstage projects")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User with ID: {current_user_id} is not allowed to unstage projects",
        )

    # Check if project exists
    project_data = get_project(project_id, session)
    if not project_data:
        logger.error(f"Unable to find project with ID: {project_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unable to find project with ID: {project_id}",
        )

    # Check if project is in STAGED status
    if project_data.status != ProjectStatus.STAGED.value:
        logger.error(f"Project with ID: {project_id} is not currently staged.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project with ID: {project_id} is not currently staged.",
        )

    try:
        unstage_project_service(
            project_id=project_id,
            current_user_id=current_user_id,
            session=session,
        )
        logger.info(f"Project ({project_id}) status set to unstaged, audit entry added and transaction committed.")

    except ValueError as ve:  # Catch specific business logic errors from services
        logger.error(f"ValueError during unstaging project {project_id}: {ve}", exc_info=True)
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve),
        )
    except Exception as e:
        logger.error(f"Unhandled error during unstaging project {project_id}: {e}", exc_info=True)
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while unstaging the project.",
        )
