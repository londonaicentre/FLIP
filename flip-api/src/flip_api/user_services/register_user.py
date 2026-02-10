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
from flip_api.db.models.user_models import PermissionRef, User
from flip_api.domain.interfaces.user import IRegisterUser, IUserResponse
from flip_api.utils.cognito_helpers import create_cognito_user, get_all_roles, get_user_pool_id, validate_roles
from flip_api.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])


# TODO [#114] This endpoint was not defined in the old repo, it was run from the step function 'registerUser'.
@router.post("/", response_model=IUserResponse)
def register_user(
    user_data: IRegisterUser,
    request: Request,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
):
    """
    Register a new user in Cognito.

    Note this function does not assign the roles to the user.

    Args:
        user_data: User data to register
        request: FastAPI request object
        db: Database session
        token_id: ID of authenticated user

    Returns:
        Created user data with ID

    Raises:
        HTTPException: If the user does not have permission to register a user, if the email is already registered, or
        if there is an error registering the user in Cognito or the database.
    """
    try:
        # Check permissions
        if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
            logger.error(f"User with ID: {token_id} attempted to register a user without permission")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {token_id} was unable to register a user"
            )

        # Validate roles
        available_roles = get_all_roles(db)
        validate_roles(user_data.roles, available_roles)

        # Get user pool ID
        user_pool_id = get_user_pool_id(request)

        # Create user in Cognito
        user_id = create_cognito_user(user_data.email, user_pool_id)

        # Create user object
        user = User(
            id=user_id,
            email=user_data.email,
            enabled=True,
        )
        try:
            db.add(user)
            db.commit()
            db.refresh(user)
        except Exception as e:
            db.rollback()
            logger.error(f"Error creating user in database: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"User with email {user_data.email} already exists."
            )

        # Return response
        response = IUserResponse(
            email=user_data.email,
            roles=user_data.roles,
            user_id=user_id,
        )  # type: ignore[call-arg]

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error registering user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {str(e)}"
        )
