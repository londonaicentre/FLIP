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
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from flip_api.auth.access_manager import can_modify_model
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.fl_services.services.fl_service import abort_model_training
from flip_api.model_services.services.model_service import delete_model, get_model_status
from flip_api.utils.logger import logger

router = APIRouter(prefix="/model", tags=["model_services"])


# [#114] ✅
@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_model_endpoint(
    request: Request,
    model_id: UUID = Path(..., title="Model ID"),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> None:
    """
    Delete a specific model.

    Args:
        request (Request): The incoming HTTP request.
        model_id (UUID): The ID of the model to delete.
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        None

    Raises:
        HTTPException: If the user is not allowed, if the model does not exist, if the model is
                       already deleted, or if there is a database error.
    """
    logger.info(f"User {user_id} requested deletion of model {model_id}")

    # Check if user has access to the model
    if not can_modify_model(user_id, model_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {user_id} is not allowed to modify this model"
        )

    # Check model status
    status_result = get_model_status(model_id, db)
    if not status_result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Model ID: {model_id} does not exist")

    if status_result.deleted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Model ID: {model_id} already deleted")

    # Try to delete model
    try:
        delete_model(model_id, user_id, db)

        abort_model_training(request, model_id, db)

    except SQLAlchemyError:
        error_message = "Database error occurred during model deletion."
        logger.exception(error_message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)

    except HTTPException:
        # Author-written 4xx messages (403/404/400) are intentional and safe;
        # only genuinely unexpected exceptions get a generic message below.
        raise
    except Exception:
        error_message = "Unexpected error occurred during model deletion"
        logger.exception(error_message)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message,
        )
