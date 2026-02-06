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

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from flip_api.auth.access_manager import can_access_model
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import ModelTrustIntersect, Trust
from flip_api.model_services.services.model_service import get_model_status
from flip_api.utils.constants import SERVICE_UNAVAILABLE_MESSAGE
from flip_api.utils.logger import logger
from flip_api.utils.site_manager import is_deployment_mode_enabled

router = APIRouter(prefix="/model", tags=["model_services"])


# [#114] ✅
@router.get("/{model_id}/trusts", status_code=status.HTTP_200_OK)
def retrieve_trusts_in_model_endpoint(
    model_id: UUID = Path(..., title="Model ID"),
    db: Session = Depends(get_session),
    user_id: Optional[UUID] = Depends(verify_token),
) -> None:
    """
    Retrieve trusts associated with a specific model.

    Args:
        model_id (UUID): The ID of the model to retrieve trusts for.
        db (Session): Database session.
        user_id (Optional[UUID]): User ID from authentication, if available.

    Returns:
        None

    Raises:
        HTTPException: If the user does not have access to the model, if the model does not exist, or if there is a
                       database error.
    """
    try:
        logger.info(f"Retrieving trusts for model {model_id} with user {user_id}")

        if user_id:
            if is_deployment_mode_enabled(db):
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=SERVICE_UNAVAILABLE_MESSAGE)

            if not can_access_model(user_id, model_id, db):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"User with ID: {user_id} is denied access to this model",
                )
        else:
            # TODO implement check_authorization_token
            # if not check_authorization_token(request):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="No user_id given, Authorization token is invalid."
            )

        status_result = get_model_status(model_id, db)
        if not status_result or status_result.deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Model ID: {model_id} does not exist")

        # Join ModelTrustIntersect -> Trust
        result = db.exec(
            select(Trust.id, Trust.name, Trust.endpoint, ModelTrustIntersect.fl_client_endpoint)
            .join(ModelTrustIntersect, ModelTrustIntersect.trust_id == Trust.id)  # type: ignore[arg-type]
            .where(ModelTrustIntersect.model_id == model_id)
        ).all()

        logger.info(f"Found {len(result)} trusts for model {model_id}")
        logger.info(f"Output: {result}")

    except SQLAlchemyError:
        error_message = "Database error occurred while retrieving trusts."
        logger.error(error_message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)

    except Exception as e:
        error_message = f"Unexpected error occurred while retrieving trusts: {str(e)}"
        logger.error(error_message)
        raise HTTPException(
            status_code=e.status_code if hasattr(e, "status_code") else status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message,
        )
