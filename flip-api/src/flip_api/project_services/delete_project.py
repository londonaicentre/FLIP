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

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_project
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.fl_services.services.fl_service import abort_model_training
from flip_api.project_services.services.image_service import delete_imaging_project, get_imaging_projects
from flip_api.project_services.services.project_services import delete_project, get_project_models_service
from flip_api.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])


# [#114] ✅
@router.delete(
    "/{project_id}",
    summary="Delete a project.",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_project_endpoint(
    request: Request,
    project_id: UUID = Path(..., description="The ID of the project to delete."),
    user_id: UUID = Depends(verify_token),
    db: Session = Depends(get_session),
) -> None:
    """
    Deletes a project with the provided ID.

    Args:
        project_id (UUID): The ID of the project to delete.
        user_id (UUID): The ID of the user making the request.
        db (Session): The database session.

    Returns:
        ProjectDetails: The details of the deleted project.

    Raises:
        HTTPException: If the user does not have permission to delete projects, if the project does not exist, or if
                       there are validation errors.
    """
    logger.debug(f"Attempting to delete project by user: {user_id}")

    # Check user permissions
    if not can_access_project(user_id, project_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {user_id} is denied access to this project"
        )

    # Get imaging projects from trusts and delete them
    trusts_imaging_projects = get_imaging_projects(project_id, db)

    for imaging_project in trusts_imaging_projects:
        delete_imaging_project(imaging_project, db)

    # Get the project models and abort training if necessary
    project_models, _ = get_project_models_service(project_id, db, all_results=True)
    logger.debug(f"Project models to be deleted: {project_models}")

    for model in project_models.data:
        abort_model_training(request=request, model_id=model.id, session=db)

    # Call delete project service
    delete_project(project_id, user_id, db)

    return
