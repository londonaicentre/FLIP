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
from flip_api.domain.schemas.status import ModelStatus
from flip_api.fl_services.services.fl_service import abort_model_training
from flip_api.model_services.services.model_service import update_model_status

router = APIRouter(prefix="/fl", tags=["fl_services"])


# [#114] ✅
@router.post("/stop/{model_id}", response_model=None)
@router.post("/stop/{model_id}/{target}", response_model=None)
@router.post("/stop/{model_id}/{target}/{clients}", response_model=None)
def stop_training(
    model_id: UUID,
    request: Request,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> None:
    """
    Stop the training of a specific model.

    Args:
        model_id (UUID): The ID of the model to stop training.
        request (Request): The incoming HTTP request.
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        None

    Raises:
        HTTPException: If the user does not have access to the model or if there is an error while stopping training.
    """
    if not can_access_model(user_id, model_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User with ID: {user_id} is denied access to this model and cannot stop training",
        )

    try:
        abort_model_training(request, model_id, db)
        update_model_status(model_id, ModelStatus.STOPPED, db)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while stopping model training: {str(e)}",
        )

    return {}, status.HTTP_204_NO_CONTENT
