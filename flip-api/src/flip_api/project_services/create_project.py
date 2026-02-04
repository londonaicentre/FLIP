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

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlmodel import Session

from flip_api.auth.auth_utils import has_permissions
from flip.auth.dependencies import verify_token
from flip.db.database import get_session
from flip.db.models.user_models import PermissionRef
from flip.domain.interfaces.shared import IId
from flip.domain.schemas.projects import ProjectDetails
from flip.project_services.services.project_services import (
    create_project,
)
from flip.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])


# [#114] ✅
@router.post(
    "/",
    summary="Create a new project.",
    response_model=IId,
    status_code=status.HTTP_201_CREATED,
)
def create_project_endpoint(
    payload: ProjectDetails = Body(...),
    user_id: UUID = Depends(verify_token),
    db: Session = Depends(get_session),
):
    """
    Creates a new project with the provided details.

    Args:
        payload (ProjectDetails): The details of the project to create.
        user_id (UUID): The ID of the user making the request.
        db (Session): The database session.

    Returns:
        ProjectDetails: The created project details.

    Raises:
        HTTPException: If the user does not have permission to create projects, if the project details are invalid,
                       or if there is an error during project creation.
    """
    logger.debug(f"Attempting to create project by user: {user_id}")

    # 1. Check user permissions
    if not has_permissions(user_id, [PermissionRef.CAN_MANAGE_PROJECTS], db):
        logger.error(f"User {user_id} does not have permission to create projects.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User with ID: {user_id} was unable to create this project",
        )

    # 2. Validate the payload
    if not payload.name:
        logger.error("Project name is required.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project name is required.",
        )

    # 3. Create the project
    try:
        project_id = create_project(
            payload=payload,
            current_user_id=user_id,
            session=db,
        )
        logger.info(f"Project created successfully: {project_id}")
        return IId(id=project_id)

    except Exception as e:
        logger.error(f"Error creating project: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while creating the project.",
        ) from e
