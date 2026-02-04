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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_project
from flip.auth.dependencies import verify_token
from flip.db.database import get_session
from flip.domain.interfaces.project import IModelsInfoResponse
from flip.project_services.services.project_services import get_project, get_project_models_service
from flip.utils.logger import logger
from flip.utils.paging_utils import IPagedData, get_total_pages

router = APIRouter(prefix="/projects", tags=["project_services"])


# [#114] ✅
@router.get(
    "/{project_id}/models",
    summary="Get the models of an imaging project.",
    response_model=IPagedData[IModelsInfoResponse],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "model": IModelsInfoResponse,
            "description": "The models of the imaging project.",
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
def get_models(
    request: Request,
    project_id: UUID,
    session: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> IPagedData[IModelsInfoResponse]:
    """
    Get the models associated with a project.

    Args:
        request (Request): The HTTP request object, used to access query parameters.
        project_id (UUID): The unique identifier of the project.
        session (Session): The database session for querying.
        user_id (UUID): The ID of the user making the request.

    Returns:
        IPagedData[IModelsInfoResponse]: A paginated response containing the models of the project.

    Raises:
        HTTPException: If the project is not found or if the user does not have permission to access it.
    """
    # Check if the user has access to the project
    if not can_access_project(user_id, project_id, session):
        logger.error(f"User ID: {user_id} does not have access to Project ID: {project_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User with ID: {user_id} is denied access to this project",
        )

    # Get the project
    project = get_project(project_id, session)
    if not project:
        logger.error(f"Project with ID: {project_id} not found.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with ID: {project_id} not found.",
        )

    # Get the models associated with the project
    models, paging_info = get_project_models_service(project_id, session, dict(request.query_params))
    print(f"Models retrieved for project ID: {project_id}: {models}")
    if not models.data:
        # This is a valid case where no models are found for the project yet
        logger.info(f"No models found for project ID: {project_id}")
    else:
        logger.info(f"Models found for project ID: {project_id}: {models}")

    # Calculate total pages based on the total rows and page size
    total_pages = get_total_pages(models.total_rows, paging_info.page_size)

    # Convert the models to the response format
    models_response: IPagedData[IModelsInfoResponse] = IPagedData(
        page=paging_info.page_number,
        page_size=paging_info.page_size,
        total_pages=total_pages,
        total_records=models.total_rows,
        data=models.data,
    )  # type: ignore[call-arg]

    logger.info(f"Models response for project ID: {project_id}: {models_response}")
    return models_response
