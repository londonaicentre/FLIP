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

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.interfaces.project import IUpdateXnatProfile
from flip_api.domain.schemas.users import Disabled
from flip_api.project_services.services.image_service import update_xnat_user_profile
from flip_api.utils.cognito_helpers import get_user_pool_id, get_username, update_user
from flip_api.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])


# [#114] ✅
@router.put(
    "/{user_id}",
    summary="Update user",
    description="Update a user with disabled status",
    response_model=Disabled,
    status_code=status.HTTP_200_OK,
)
def update_user_endpoint(
    user_id: UUID,
    disabled: Disabled,
    request: Request,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> Disabled:
    """
    Update user details with disabled status

    Requires CAN_MANAGE_USERS permission

    Args:
        user_id (UUID): The ID of the user to update
        disabled (Disabled): The disabled status to set for the user
        request (Request): The FastAPI request object
        db (Session): The database session
        token_id (UUID): The ID of the token used for authentication

    Returns:
        Disabled: The updated disabled status of the user

    Raises:
        HTTPException: If the user does not have permission to update a user, if the user is not found, or if there is
        an error updating the user in Cognito or the database.
    """
    try:
        # Check if user has permission to manage users
        if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
            logger.error(f"User with ID: {token_id} was unable to manage users")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {token_id} was unable to manage users"
            )

        # Get user pool ID
        user_pool_id = get_user_pool_id(request)

        # Get username from user ID
        username = get_username(str(user_id), user_pool_id)

        if not username:
            err_msg = f"User could not be found with id: {user_id}"
            logger.error(err_msg)
            raise HTTPException(status_code=404, detail=err_msg)

        # Update user in Cognito
        response = update_user(username, user_pool_id, disabled.disabled)

        # Update XNAT user profile
        set_user_enabled_data = IUpdateXnatProfile(
            email=username,
            enabled=not disabled.disabled,
        )

        update_xnat_user_profile(set_user_enabled_data, db)

        logger.info(f"User updated successfully: {response}")
        return response

    except HTTPException:
        # Re-raise HTTP exceptions
        raise

    except Exception as e:
        logger.error(f"Failed to update user: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
