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

from flip_api.auth.dependencies import verify_token
from flip.db.database import get_session
from flip.domain.interfaces.model import IModelResponse
from flip.model_services.retrieve_model import retrieve_model
from flip.model_services.update_model_status import update_model_status
from flip.utils.logger import logger

router = APIRouter(prefix="/step", tags=["step_functions_services"])


@router.post("/model/{model_id}", response_model=IModelResponse, status_code=status.HTTP_200_OK)
def retrieve_model_step_function_endpoint(
    model_id: UUID,
    request: Request,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
):
    """
    Retrieve model by ID, checking and updating its status first

    This mimics the AWS Step Functions workflow defined in retrieveModel.yml

    Args:
        model_id (UUID): The ID of the model to retrieve.
        request (Request): The FastAPI request object.
        db (Session): The database session.
        user_id (str): The ID of the current user.

    Returns:
        IModelResponse: The response containing the model details.

    Raises:
        HTTPException: If an error occurs during any step of the process.
    """
    try:
        # Step 1: Retrieve Model Status
        logger.info(f"Retrieving status for model: {model_id}")

        # TODO: Implement actual logic to retrieve model status from logs
        # model_status = retrieve_model_status_from_logs(model_id=model_id, db=db, user_id=user_id)

        # logger.info(f"Found status {model_status} for model {model_id} in logs")

        # Step 2: Update Model Status
        if not update_model_status(model_id=model_id, status=None, session=db):
            error_message = "Failed to update model status"
            logger.error(error_message)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_message,
            )

        # Step 3: Retrieve Model
        logger.info(f"Retrieving model: {model_id}")

        model_response = retrieve_model(model_id=model_id, db=db, user_id=user_id)

        return model_response

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.exception(f"Unhandled error in retrieve_model_endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve model: {str(e)}")
