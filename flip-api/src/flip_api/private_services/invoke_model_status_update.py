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
from sqlmodel import Session

from flip_api.auth.access_manager import authenticate_internal_service
from flip_api.db.database import get_session
from flip_api.domain.schemas.status import ModelStatus
from flip_api.model_services.update_model_status import update_model_status_endpoint
from flip_api.utils.logger import logger

router = APIRouter(tags=["private_services"])


# [#114] ✅
@router.put(
    "/model/{model_id}/status/{model_status}",
    summary="Invoke model status update process",
    response_model=dict[str, str],
)
def invoke_model_status_update_endpoint(
    model_id: UUID,
    model_status: ModelStatus = Path(..., title="New model status"),
    db: Session = Depends(get_session),
    _: None = Depends(authenticate_internal_service),
) -> dict[str, str]:
    """
    Invokes the internal process for updating a model's status.

    This endpoint is internal-only: it accepts requests from the fl-server on the
    Central Hub (authenticated via INTERNAL_SERVICE_KEY_HEADER).

    Args:
        model_id (UUID): The ID of the model whose status is to be updated.
        model_status (ModelStatus): The new status to set for the model.
        db (Session): The database session, provided by dependency injection.

    Returns:
        dict[str, str]: A dictionary containing the result of the status update operation.

    Raises:
        HTTPException: If there is an error during the status update process.
    """
    endpoint_path = f"/model/{model_id}/status/{model_status.value}"
    logger.debug(f"Attempting to call the model status update service for model_id: {model_id} via {endpoint_path}")

    try:
        response_data = update_model_status_endpoint(model_id=model_id, model_status=model_status, db=db, user_id=None)

        logger.info(f"Model status update service called and executed successfully for model_id: {model_id}")
        return response_data

    except HTTPException as http_exc:
        # If perform_model_status_update raises an HTTPException, re-raise it
        logger.warning(f"HTTPException from service for model {model_id} in {endpoint_path}: {http_exc.detail}")
        raise http_exc
    except Exception as e:
        logger.error(f"Unhandled error in {endpoint_path}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while invoking model status update.",
        )
