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
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_model
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.model import IModelMetrics
from flip_api.model_services.services.model_service import get_metrics, get_model_status
from flip_api.utils.logger import logger

router = APIRouter(prefix="/model", tags=["model_services"])


# [#114] ✅
@router.get("/{model_id}/metrics", response_model=List[IModelMetrics], status_code=status.HTTP_200_OK)
def get_metrics_endpoint(
    model_id: UUID = Path(..., title="Model ID"),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
):
    """
    Retrieve metrics for a specific model.

    Args:
        model_id (UUID): The ID of the model to retrieve metrics for.
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        List[IModelMetrics]: A list of metrics associated with the specified model.

    Raises:
        HTTPException: If the user does not have access to the model, if the model does not exist, or if there is a
                       database error.
    """
    logger.info(f"User {user_id} requested metrics for model {model_id}")

    # Check access permissions
    if not can_access_model(user_id, model_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {user_id} is denied access to this model"
        )

    # Check model existence
    status_result = get_model_status(model_id, db)
    if not status_result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Model ID: {model_id} does not exist")

    try:
        metrics = get_metrics(model_id, db)
        return metrics

    except SQLAlchemyError:
        error_message = "Database error occurred while fetching metrics."
        logger.error(error_message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)

    except Exception as e:
        error_message = f"Unexpected error occurred while fetching metrics: {str(e)}"
        logger.error(error_message)
        raise HTTPException(
            status_code=e.status_code if hasattr(e, "status_code") else status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message,
        )
