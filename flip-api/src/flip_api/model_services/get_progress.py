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
from flip_api.domain.interfaces.model import IModelProgress
from flip_api.model_services.services.model_service import get_model_status
from flip_api.model_services.services.progress import get_model_progress
from flip_api.utils.logger import logger

router = APIRouter(prefix="/model", tags=["model_services"])


@router.get("/{model_id}/progress", response_model=IModelProgress, status_code=status.HTTP_200_OK)
def get_progress_endpoint(
    model_id: UUID = Path(..., title="Model ID"),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> IModelProgress:
    """
    Retrieve the derived federated-round progress for a model.

    Round position, timing estimates and the per-trust ladder are computed
    server-side from the typed ``fl_logs`` events (see ``services/progress.py``)
    so the UI stays a pure renderer. Models with no round events (e.g. trained
    before event emission existed) return an empty shell with the roster only.

    Args:
        model_id (UUID): The ID of the model to derive progress for.
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        IModelProgress: The round-progress view for the model.

    Raises:
        HTTPException: If the user does not have access to the model, if the
            model does not exist, or if there is a database error.
    """
    logger.info(f"User {user_id} requested round progress for model {model_id}")

    if not can_access_model(user_id, model_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {user_id} is denied access to this model"
        )

    status_result = get_model_status(model_id, db)
    if not status_result or status_result.deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Model ID: {model_id} does not exist")

    try:
        return get_model_progress(model_id, db)
    except SQLAlchemyError:
        error_message = "Database error while deriving model progress."
        logger.error(error_message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)
