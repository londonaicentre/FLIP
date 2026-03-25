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

from flip_api.auth.access_manager import can_access_project
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.project import IImagingStatus
from flip_api.project_services.services.image_service import (
    base64_url_encode,
    get_imaging_project_statuses,
    get_imaging_projects,
)
from flip_api.project_services.services.project_services import get_project
from flip_api.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])


# [#114] ✅
@router.get(
    "/{project_id}/image/status",
    summary="Get the status of an imaging project.",
    response_model=list[IImagingStatus],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "model": list[IImagingStatus],
            "description": "The status of the imaging project.",
        },
        status.HTTP_403_FORBIDDEN: {
            "model": None,
            "description": "You do not have permission to access this project.",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": None,
            "description": "The project was not found.",
        },
    },
)
async def get_imaging_project_status(
    project_id: UUID,
    session: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> list[IImagingStatus]:
    """
    Get the status of an imaging project.

    Args:
        project_id (UUID): The ID of the project.
        session (Session): The database session.
        user_id (UUID): The ID of the user.

    Returns:
        ImagingProject: The status of the imaging project.

    Raises:
        HTTPException: If the user does not have permission to access the project, if the project is not found, or if
        there is an error retrieving the imaging project status.
    """
    logger.info(f"Getting imaging project status for project {project_id}")

    # Check if the user has access to the project
    if not can_access_project(user_id, project_id, session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this project.",
        )

    # Get the project from the database
    project_response = get_project(project_id, session)

    # Check if the project query exists
    if not project_response.query:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The project query was not found.",
        )
    # Get imaging projects
    if not project_response.query.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The project query ID was not found.",
        )
    imaging_projects = get_imaging_projects(project_id, session)
    if not imaging_projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The imaging project was not found.",
        )
    # Get imaging project statuses
    if not project_response.query.query:
        project_response.query.query = ""
    encoded_query = base64_url_encode(project_response.query.query)
    imaging_project_statuses = get_imaging_project_statuses(imaging_projects, encoded_query, session)
    if not imaging_project_statuses:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The imaging project status was not found.",
        )
    # Return the imaging project statuses
    return imaging_project_statuses
