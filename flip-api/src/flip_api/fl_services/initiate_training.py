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

from flip_api.auth.access_manager import can_access_model
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.fl import IInitiateTrainingInputPayload
from flip_api.domain.schemas.status import ModelStatus
from flip_api.fl_services.services.fl_service import add_fl_job
from flip_api.model_services.services.model_service import add_log, update_model_status
from flip_api.utils.logger import logger

router = APIRouter(prefix="/fl", tags=["fl_services"])


# [#114] ✅
@router.post("/initiate/{model_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def initiate_training(
    model_id: UUID,
    payload: IInitiateTrainingInputPayload,
    request: Request,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> None:
    """
    Initiate training for a model by adding it to the queue.

    This endpoint allows a user to initiate training for a specified model by adding it to the training queue.
    It checks if the user has access to the model and updates the model status accordingly.

    Args:
        model_id (UUID): The ID of the model to initiate training for.
        payload (IInitiateTrainingInputPayload): The payload containing trusts to be used for training.
        request (Request): The FastAPI request object.
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        None

    Raises:
        HTTPException: If the user does not have access to the model, if the model does not exist, or if there is an
                        error during the initiation process.
    """
    logger.debug(f"Initiating training for model ID: {model_id} by user ID: {user_id} with payload: {payload}")

    if not can_access_model(user_id, model_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {user_id} is denied access to this model"
        )

    try:
        add_fl_job(model_id, payload.trusts, db)

        updated = update_model_status(model_id, ModelStatus.INITIATED, db)
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Model ID: {model_id} does not exist")

        add_log(model_id, "This model has been added to the queue.", db)
        add_log(model_id, f"Selected trusts for training: {payload.trusts}", db)

    except HTTPException:
        raise  # re-raise known errors
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred during training initiation: {str(e)}",
        )
