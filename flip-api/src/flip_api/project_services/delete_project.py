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

from flip_api.auth.access_manager import can_modify_project
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.fl_services.services.fl_service import abort_model_training
from flip_api.project_services.services.project_services import delete_project, get_project_models_service
from flip_api.utils.logger import logger

router = APIRouter(prefix="/projects", tags=["project_services"])


# [#114] ✅
@router.delete(
    "/{project_id}",
    summary="Delete a project.",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
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
        request (Request): FastAPI request object.
        project_id (UUID): The ID of the project to delete.
        user_id (UUID): The ID of the user making the request.
        db (Session): The database session.

    Returns:
        None

    Raises:
        HTTPException: If the user is not allowed, if the project does not exist, or if there are validation errors.
    """
    logger.debug(f"Attempting to delete project by user: {user_id}")

    # Check user permissions
    if not can_modify_project(user_id, project_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User with ID: {user_id} is not allowed to modify this project",
        )

    # Trust imaging is deliberately left in place. Deleting a project is a *soft* delete on the hub
    # (the row is retained and audited), but removing the Trust's XNAT project would be
    # irreversible: images could be re-pulled from PACS, while the segmentations, contours and
    # annotations added during data enrichment could not. Imaging removal, when it is wanted,
    # belongs in an explicit purge with its own confirmation — not as a silent cascade off a
    # soft delete. See FLIP#963.
    #
    # Two consequences, so neither is rediscovered later:
    #   * Retention is open-ended. That purge does not exist yet (tracked in FLIP#997), and there is
    #     no TTL, retention sweep or admin endpoint either — so imaging for every deleted project
    #     persists until someone acts directly in XNAT.
    #   * Retained imaging is not re-adopted. Nothing sets `Projects.deleted` back to False, and a
    #     re-created project gets a new id, so imaging-api would create a fresh XNAT project while
    #     the old one stays keyed to the deleted project's id and unreachable from the UI.

    # Get the project models and abort training if necessary
    project_models, _ = get_project_models_service(project_id, db, all_results=True)
    logger.debug(f"Project models to be deleted: {project_models}")

    for model in project_models.data:
        abort_model_training(request=request, model_id=model.id, session=db)

    # Call delete project service
    delete_project(project_id, user_id, db)
