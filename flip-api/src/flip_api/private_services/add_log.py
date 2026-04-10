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
from sqlmodel import Session

from flip_api.auth.access_manager import authenticate_internal_service
from flip_api.db.database import get_session
from flip_api.domain.schemas.private import TrainingLog
from flip_api.model_services.services.model_service import add_log, validate_trusts
from flip_api.utils.logger import logger

router = APIRouter(tags=["private_services"])


# [#114] ✅
@router.post("/model/{model_id}/logs", response_model=dict[str, str])
def add_log_endpoint(
    model_id: UUID,
    training_log: TrainingLog,
    db: Session = Depends(get_session),
    _: None = Depends(authenticate_internal_service),
) -> dict[str, str]:
    """
    Add a log entry to the database for a specific model.

    This endpoint is internal-only: it accepts requests from the fl-server on the
    Central Hub (authenticated via INTERNAL_SERVICE_KEY_HEADER).

    Args:
        model_id (UUID): The ID of the model.
        training_log (TrainingLog): The log entry to be added.
        db (Session): The database session.

    Returns:
        dict[str, str]: A confirmation message indicating the log entry was created.

    Raises:
        HTTPException: If the trust is not associated with the model or if there is an internal server error.
    """
    try:
        if not validate_trusts(model_id=model_id, trusts=[training_log.trust], session=db):
            error_msg = f"The trust: {training_log.trust} is not associated with model: {model_id}"
            logger.error(error_msg)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)

        add_log(model_id=model_id, log=training_log.log, session=db)

        return {"detail": "Created"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error in add_log endpoint for model {model_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while adding the log.",
        )
