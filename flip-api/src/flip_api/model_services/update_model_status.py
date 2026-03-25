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

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_model
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.schemas.status import ModelStatus
from flip_api.model_services.services.model_service import add_log, update_model_status
from flip_api.utils.constants import SERVICE_UNAVAILABLE_MESSAGE
from flip_api.utils.logger import logger
from flip_api.utils.site_manager import is_deployment_mode_enabled

router = APIRouter(prefix="/model", tags=["model_services"])


# TODO [#114] This endpoint was not defined in the old repo. This functionality was called from the private service
# (see private_services/invoke_model_status_update.py)
@router.patch("/{model_id}/status/{model_status}", status_code=status.HTTP_200_OK, response_model=dict[str, str])
def update_model_status_endpoint(
    model_id: UUID = Path(..., title="Model ID"),
    model_status: ModelStatus = Path(..., title="New model status"),
    db: Session = Depends(get_session),
    user_id: UUID | None = Depends(verify_token),
) -> dict[str, str]:
    """
    Update the status of a specific model.

    Args:
        model_id (UUID): The ID of the model to update.
        model_status (ModelStatus): The new status to set for the model.
        db (Session): Database session.
        user_id (Optional[UUID]): User ID from authentication, if available.

    Returns:
        dict[str, str]: A success message indicating the status has been updated.

    Raises:
        HTTPException: If the user does not have access to the model, if the model does not exist, or if there is a
                       database error.
    """
    logger.info(f"Received request to update model {model_id} to status '{model_status}'")

    try:
        # This function is called from the private service so won't always contain a `userId`.
        # If no `userId` exists check we've got the correct auth token.

        # Auth flow
        if user_id:
            if is_deployment_mode_enabled(db):
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=SERVICE_UNAVAILABLE_MESSAGE)

            if not can_access_model(user_id, model_id, db):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"User with ID: {user_id} is denied access to this model",
                )
        else:
            # FIXME
            # if not check_authorization_token(request):
            # raise HTTPException(
            #     status_code=status.HTTP_403_FORBIDDEN, detail="No userId passed, Authorization token is invalid."
            # )
            # Assume that if no `userId` is passed, the request is coming from a private service and the token has
            # already been validated.
            pass

        updated = update_model_status(model_id, model_status, db)
        logger.info(f"Update response: {updated}")

        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Model ID: {model_id} does not exist")

        log_message = {
            ModelStatus.ERROR: "There has been an error whilst running training for this model.",
            ModelStatus.STOPPED: "Training has been stopped for this model.",
            ModelStatus.INITIATED: "This model has been selected from the queue and will be prepared for training.",
            ModelStatus.PREPARED: "This model has been prepared and will begin training.",
            ModelStatus.TRAINING_STARTED: "Training has started for this model.",
            ModelStatus.RESULTS_UPLOADED: "The results of this model have been uploaded and can now be downloaded.",
        }.get(model_status)

        if log_message:
            add_log(
                model_id,
                log_message,
                db,
                success=model_status not in [ModelStatus.ERROR, ModelStatus.STOPPED],
            )

        logger.info(f"Status of model {model_id} updated successfully to {model_status}")
        return {"success": "status set"}

    except SQLAlchemyError:
        error_message = "Database error while updating model status."
        logger.error(error_message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)

    except Exception as e:
        error_message = f"Unexpected error while updating model status: {str(e)}"
        logger.error(error_message)
        raise HTTPException(
            status_code=e.status_code if hasattr(e, "status_code") else status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message,
        )
