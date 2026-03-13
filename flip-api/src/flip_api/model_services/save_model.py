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
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from flip_api.auth.access_manager import can_access_project
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Model, ModelTrustIntersect, ProjectTrustIntersect
from flip_api.domain.interfaces.model import ISaveModel
from flip_api.domain.interfaces.shared import IId
from flip_api.domain.schemas.status import ModelStatus, ProjectStatus, TrustIntersectStatus
from flip_api.fl_services.services.pull_required_files import pull_required_files_json_to_assets
from flip_api.utils.logger import logger
from flip_api.utils.project_manager import get_project_by_id

router = APIRouter(prefix="/model", tags=["model_services"])


# [#114] ✅
@router.post("", status_code=status.HTTP_200_OK, response_model=IId)
def save_model(
    request: Request,
    payload: ISaveModel,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> IId:
    """
    Create a model for a specific project.

    Args:
        request (Request): The incoming HTTP request.
        payload (ISaveModel): The model data to be saved, including name, description, and project ID.
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        IId: An object containing the ID of the created model.

    Raises:
        HTTPException: If the user does not have access to the project, if the project does not exist, if the project
                       is not approved, if there are no approved trusts for the project, or there is a database error.
    """
    logger.info(f"User {user_id} requested model creation for project {payload.project_id}")

    # Access control
    if not can_access_project(user_id, payload.project_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {user_id} is denied access to this project"
        )

    # Validate project
    project = get_project_by_id(payload.project_id, db)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No project found for ID {payload.project_id}"
        )

    if project.status != ProjectStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Project {payload.project_id} is not approved"
        )

    # Find approved trusts for this project
    approved_trusts = db.exec(
        select(ProjectTrustIntersect.trust_id).where(
            ProjectTrustIntersect.project_id == payload.project_id, ProjectTrustIntersect.approved
        )
    ).all()

    if not approved_trusts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"No approved trusts found for project {payload.project_id}"
        )

    logger.debug(f"Found {len(approved_trusts)} approved trusts")

    try:
        # Create model
        model = Model(
            name=payload.name,
            description=payload.description or "",
            status=ModelStatus.PENDING,
            project_id=payload.project_id,
            owner_id=user_id,
        )
        db.add(model)
        db.flush()  # Ensure model.id is available

        # Insert trust relationships
        for trust in approved_trusts:
            intersect = ModelTrustIntersect(
                model_id=model.id,
                trust_id=trust if trust else None,
                status=TrustIntersectStatus.PENDING,
            )
            db.add(intersect)
        db.commit()

        # Pull the latest required_files.json from S3 after model creation
        try:
            pull_required_files_json_to_assets()
        except Exception as e:
            logger.error(f"Failed to pull required_files.json from S3: {e}")

        logger.info(f"Model created with ID: {model.id}")
        return IId(id=model.id)

    except SQLAlchemyError as e:
        error_message = f"Database error occurred while saving model: {str(e)}"
        logger.error(error_message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)

    except Exception as e:
        error_message = f"Unexpected error occurred while saving model: {str(e)}"
        logger.error(error_message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)
