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
from sqlmodel import Session, col, select

from flip_api.auth.access_manager import can_modify_model
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust
from flip_api.domain.interfaces.fl import IInitiateTrainingInputPayload
from flip_api.domain.schemas.status import ModelStatus
from flip_api.fl_services.services.fl_scheduler_service import log_queue_positions
from flip_api.fl_services.services.fl_service import add_fl_job
from flip_api.model_services.services.model_service import add_log, update_model_status
from flip_api.utils.constants import SERVICE_UNAVAILABLE_MESSAGE
from flip_api.utils.logger import logger
from flip_api.utils.site_manager import is_deployment_mode_enabled

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
        payload (IInitiateTrainingInputPayload): The payload containing the ids of the trusts to train on.
        request (Request): The FastAPI request object.
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        None

    Raises:
        HTTPException: If the user is not allowed, if the model does not exist, or if there is an
                        error during the initiation process.
    """
    logger.debug(f"Initiating training for model ID: {model_id} by user ID: {user_id} with payload: {payload}")

    if is_deployment_mode_enabled(db):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=SERVICE_UNAVAILABLE_MESSAGE)

    if not can_modify_model(user_id, model_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {user_id} is not allowed to modify this model"
        )

    trusts = db.exec(select(Trust).where(col(Trust.id).in_(payload.trust_ids))).all()
    known_ids = {t.id for t in trusts}
    missing = [str(trust_id) for trust_id in payload.trust_ids if trust_id not in known_ids]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown trust(s): {missing}",
        )

    try:
        add_fl_job(model_id, list(trusts), db)

        updated = update_model_status(model_id, ModelStatus.INITIATED, db)
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Model ID: {model_id} does not exist")

        # One typed row per queue movement: this emits the new job's initial
        # position ("Model Queued (n)"), replacing the old free-text enqueue line.
        log_queue_positions(db)
        add_log(model_id, f"Selected trusts for training: {', '.join(t.name for t in trusts)}", db)

    except HTTPException:
        raise  # re-raise known errors
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred during training initiation: {str(e)}",
        )
