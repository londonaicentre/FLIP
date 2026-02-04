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

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

from flip_api.auth.access_manager import can_access_model
from flip.auth.dependencies import verify_token
from flip.db.database import get_session
from flip.db.models.main_models import FLLogs, Model, Projects
from flip.domain.interfaces.model import ILog
from flip.model_services.services.model_service import get_model_status
from flip.utils.logger import logger

router = APIRouter(prefix="/model", tags=["model_services"])


# [#114] ✅
@router.get("/{model_id}/logs", response_model=List[ILog], status_code=status.HTTP_200_OK)
def retrieve_logs_for_model_endpoint(
    model_id: UUID = Path(..., title="Model ID"),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
):
    """
    Retrieve logs for a specific model.

    Args:
        model_id (UUID): The ID of the model to retrieve logs for.
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        List[FLLogs]: A list of logs associated with the specified model.

    Raises:
        HTTPException: If the user does not have access to the model, if the model does not exist, or if there is a
                       database error.
    """
    logger.info(f"User {user_id} requested logs for model {model_id}")

    # Access control
    if not can_access_model(user_id, model_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {user_id} is denied access to this model"
        )

    # Check model status
    status_result = get_model_status(model_id, db)
    if not status_result or status_result.deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Model ID: {model_id} does not exist")

    try:
        # Ensure model is linked to non-deleted project
        model = db.exec(
            select(Model)
            .where(Model.id == model_id)
            .join(Projects, Projects.id == Model.project_id)  # type: ignore[arg-type]
            .where(col(Projects.deleted).is_(False))
        ).first()

        if not model:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Model ID: {model_id} does not exist or is orphaned"
            )

        # Retrieve logs using ORM
        logs = db.exec(select(FLLogs).where(FLLogs.model_id == model_id)).all()

        logger.info(f"Retrieved {len(logs)} log(s) for model {model_id}")

        return [
            ILog(
                id=log.id,
                model_id=log.model_id,
                log_date=log.log_date,
                success=log.success,
                trust_name=log.trust_name,
                log=log.log,
            )  # type: ignore[call-arg]
            for log in logs
        ]

    except SQLAlchemyError:
        error_message = "Database error while retrieving logs."
        logger.error(error_message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)

    except Exception as e:
        error_message = f"Unexpected error while retrieving logs: {str(e)}"
        logger.error(error_message)
        raise HTTPException(
            status_code=e.status_code if hasattr(e, "status_code") else status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message,
        )
