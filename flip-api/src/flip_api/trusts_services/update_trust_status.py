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

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, status
from sqlmodel import Session, select

from flip_api.auth.access_manager import can_access_model
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import ModelTrustIntersect, Trust, TrustIntersectStatus
from flip_api.domain.schemas.trusts import UpdateTrustStatusSchema
from flip_api.utils.constants import SERVICE_UNAVAILABLE_MESSAGE
from flip_api.utils.logger import logger
from flip_api.utils.site_manager import is_deployment_mode_enabled

router = APIRouter(prefix="/trust", tags=["trusts_services"])


def check_model_exists(model_id: UUID, db: Session) -> bool:
    """
    Check if a model exists in the database.

    Args:
        model_id (UUID): ID of the model to check.
        db (Session): Database session.

    Returns:
        bool: True if the model exists, False otherwise.
    """
    statement = select(ModelTrustIntersect).where(ModelTrustIntersect.model_id == model_id)
    return db.exec(statement).first() is not None


def check_trust_exists(trust_id: str, db: Session) -> bool:
    """
    Check if a trust exists in the database.

    Args:
        trust_id (str): ID of the trust to check.
        db (Session): Database session.

    Returns:
        bool: True if the trust exists, False otherwise.
    """
    statement = select(ModelTrustIntersect).where(ModelTrustIntersect.trust_id == trust_id)
    return db.exec(statement).first() is not None


# [#114] ✅
@router.put("/{trust_id}/model/{model_id}/status/{trust_status}", response_model=dict[str, str])
def update_trust_status(
    request: Request,
    trust_id: str = Path(..., description="ID of the trust"),
    model_id: UUID = Path(..., description="ID of the model"),
    trust_status: str = Path(..., description="New status to set"),
    data: Optional[UpdateTrustStatusSchema] = Body(None),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> dict[str, str]:
    """
    Update the status of a trust intersect with a model.

    Args:
        request (Request): FastAPI request object.
        trust_id (str): ID of the trust.
        model_id (UUID): ID of the model.
        trust_status (str): New status to set.
        data (Optional[UpdateTrustStatusSchema]): Request body containing additional data.
        db (Session): Database session.
        user_id (UUID): ID of the authenticated user.

    Returns:
        dict[str, str]: Success message.

    Raises:
        HTTPException: If the user does not have permission to update the trust status, if the model or trust does not
        exist, if the trust status is invalid, if there is an error communicating with the trust, or if there is an
        error updating the trust status in the database.
    """
    try:
        # Extract user ID from request context if available
        if hasattr(request, "state") and hasattr(request.state, "user"):
            user_id = request.state.user.sub

        # This function is called from the private service so won't always contain a `userId`.
        # If no `userId` exists check we've got the correct auth token.
        if user_id:
            # Check if deployment mode is enabled
            if is_deployment_mode_enabled(db):
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=SERVICE_UNAVAILABLE_MESSAGE)

            # Check if user can access the model
            if not can_access_model(user_id, model_id, db):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"User with ID: {user_id} is denied access to this model",
                )
        else:
            pass
            # TODO implement check_authorization_token
            # Check authorization token
            # if not check_authorization_token(request.headers):
            #     raise HTTPException(
            #         status_code=403,
            #         detail="No userId passed, Authorization token is invalid."
            #     )

        # Validate status - convert to uppercase and check against enum
        trust_status = trust_status.upper()
        if trust_status not in TrustIntersectStatus.__members__:
            error_msg = f"Status: {trust_status} is not a valid status"
            logger.error(error_msg)
            raise HTTPException(status_code=400, detail=error_msg)

        fl_client_endpoint = None

        # Handle INITIALISED status
        if trust_status == TrustIntersectStatus.INITIALISED:
            if not data:
                error_msg = "Request body must be populated with fl_client_endpoint when status is set to INITIALISED"
                logger.error(error_msg)
                raise HTTPException(status_code=400, detail=error_msg)

            # Get trust endpoint
            statement = select(Trust.endpoint).where(Trust.id == trust_id)
            result = db.execute(statement).scalars().first()

            if not result:
                error_msg = f"Endpoint does not exist for trust: {trust_id}"
                logger.error(error_msg)
                raise HTTPException(status_code=404, detail=error_msg)

            # Transform endpoint
            trust_endpoint = result.endpoint.split(":32472/flip")
            fl_client_endpoint = f"{trust_endpoint[0].replace('http://', '')}:{data.fl_client_endpoint}"

        # Update the ModelTrustIntersect table
        intersect = db.exec(
            select(ModelTrustIntersect).where(
                ModelTrustIntersect.model_id == model_id, ModelTrustIntersect.trust_id == trust_id
            )
        ).first()

        if not intersect:
            model_exists = check_model_exists(model_id, db)
            trust_exists = check_trust_exists(trust_id, db)

            if not model_exists and not trust_exists:
                error_msg = f"Both {model_id=} and {trust_id=} do not exist"
            elif not model_exists:
                error_msg = f"{model_id=} does not exist"
            elif not trust_exists:
                error_msg = f"{trust_id=} does not exist"
            else:
                error_msg = f"No relationship exists between {model_id=} and {trust_id=}"
            logger.error(error_msg)
            raise HTTPException(status_code=404, detail=error_msg)

        # Apply update in Python
        intersect.status = TrustIntersectStatus(trust_status)
        intersect.fl_client_endpoint = fl_client_endpoint
        db.commit()
        db.refresh(intersect)

        logger.info(f"Status of trust {trust_id} has been updated successfully")

        return {"success": "message successfully sent"}

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        error_msg = f"Error updating trust status: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)
