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

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, status
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_project
from flip.auth.dependencies import verify_token
from flip.db.database import get_session
from flip.db.models.main_models import Projects
from flip.domain.interfaces.project import IEditProject, IProjectDetails
from flip.domain.schemas.status import ProjectStatus
from flip.project_services.services.project_services import edit_project_service
from flip.utils.cognito_helpers import filter_enabled_users, get_user_pool_id
from flip.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])


# [#114] ✅
@router.put(
    "/{project_id}",
    summary="Edit a project.",
    status_code=status.HTTP_200_OK,
    response_model=Projects,
)
def edit_project_endpoint(
    request: Request,
    project_id: UUID = Path(..., description="The ID of the project to edit."),
    project_details: IEditProject = Body(..., description="Details of the project to edit."),
    user_id: UUID = Depends(verify_token),
    db: Session = Depends(get_session),
):
    """
    Edits a project with the provided ID. This endpoint allows users with the appropriate permissions to update the
    project's name and description.

    Args:
        project_id (UUID): The ID of the project to edit.
        project_name (str): The new name for the project.
        project_description (str): The new description for the project.
        user_id (UUID): The ID of the user making the request.
        db (Session): The database session.

    Returns:
        ProjectDetails: The updated project details.

    Raises:
        HTTPException: If the user does not have permission to edit projects, if the project does not exist, or if
                       there are validation errors.
    """
    logger.debug(f"Attempting to edit project by user: {user_id}")

    if not can_access_project(user_id=user_id, project_id=project_id, db=db):
        logger.error(f"User {user_id} is not allowed to edit project {project_id}.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User with ID: {user_id} is not allowed to edit this project.",
        )

    # Check if project exists and is not deleted
    project = db.get(Projects, project_id)
    if not project or project.deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with ID {project_id} does not exist or is deleted, cannot edit.",
        )

    # Validate whether project has UNSTAGED status
    if project.status != ProjectStatus.UNSTAGED:
        logger.error(f"Project {project_id} is not in UNSTAGED status.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to edit the project as it has already been staged/approved",
        )

    # Validate users
    if project_details.users:
        # Ensure that all users exist and are not disabled
        user_pool_id = get_user_pool_id(request)
        valid_users = filter_enabled_users(user_pool_id, project_details.users)
    else:
        valid_users = []

    details = IProjectDetails(
        name=project_details.name,
        description=project_details.description or "",
        users=valid_users,
    )

    # TODO If IProjectDetails had user IDs, IEditProject would not be needed.
    logger.debug(f"Editing project {project_id} with details: {details}")

    try:
        edit_project_service(project_id=project_id, payload=details, current_user_id=user_id, session=db)
        return db.get(Projects, project.id)

    except Exception as e:
        error_message = f"Error editing project {project_id}: {str(e)}"
        logger.error(error_message)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message,
        )

    except HTTPException:
        raise
