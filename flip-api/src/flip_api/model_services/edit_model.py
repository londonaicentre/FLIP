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
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_model
from flip.auth.dependencies import verify_token
from flip.db.database import get_session
from flip.domain.interfaces.model import IModelDetails, ModelStatusEdit
from flip.model_services.services.model_service import edit_model, get_model_status
from flip.utils.logger import logger

router = APIRouter(prefix="/model", tags=["model_services"])


# [#114] ✅
@router.put("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def edit_model_endpoint(
    model_id: UUID,
    model_details: IModelDetails,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
):
    # Access control
    if not can_access_model(user_id, model_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {user_id} is denied access to this model"
        )

    # Model status validation
    status_result = get_model_status(model_id, db)
    if not status_result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Model ID: {model_id} does not exist")

    # Check if model is marked as deleted
    if status_result.deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model with ID: {model_id} cannot be edited because it was deleted",
        )

    # Check if model status is not in the allowed editable statuses
    if status_result.status.value not in ModelStatusEdit.__members__:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model with ID: {model_id} cannot be edited due to its current status: {status_result.status}",
        )

    # Edit model inside transaction
    try:
        edit_model(model_id, model_details, user_id, db)

    except SQLAlchemyError:
        error_message = "Database error occurred during model edit."
        logger.error(error_message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)

    except Exception as e:
        error_message = f"Unexpected error occurred during model edit: {str(e)}"
        logger.error(error_message)
        raise HTTPException(
            status_code=e.status_code if hasattr(e, "status_code") else status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message,
        )
