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

from flip_api.auth.access_manager import authenticate_internal_service
from flip_api.db.database import get_session
from flip_api.domain.schemas.status import ModelStatus
from flip_api.model_services.services.model_service import add_log, update_model_status
from flip_api.utils.logger import logger

router = APIRouter(tags=["private_services"])


@router.put(
    "/model/{model_id}/status/{model_status}",
    summary="Update model status (internal service only)",
    response_model=dict[str, str],
)
def invoke_model_status_update_endpoint(
    model_id: UUID,
    model_status: ModelStatus = Path(..., title="New model status"),
    db: Session = Depends(get_session),
    _: None = Depends(authenticate_internal_service),
) -> dict[str, str]:
    """
    Update a model's status. Restricted to internal FL services via internal service key authentication.

    This endpoint is internal-only: it accepts requests from the fl-server on the
    Central Hub (authenticated via INTERNAL_SERVICE_KEY_HEADER).

    Args:
        model_id (UUID): The ID of the model whose status is to be updated.
        model_status (ModelStatus): The new status to set for the model.
        db (Session): The database session, provided by dependency injection.

    Returns:
        dict[str, str]: A dictionary containing the result of the status update operation.

    Raises:
        HTTPException: If the model does not exist or there is a database/server error.
    """
    logger.info(f"Received internal request to update model {model_id} to status '{model_status}'")

    try:
        updated = update_model_status(model_id, model_status, db)

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

    except HTTPException:
        raise

    except Exception as e:
        error_message = f"Unexpected error while updating model status: {str(e)}"
        logger.error(error_message, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)
