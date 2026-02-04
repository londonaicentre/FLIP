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

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from flip_api.auth.auth_utils import has_permissions
from flip.auth.dependencies import verify_token
from flip.config import get_settings
from flip.db.database import get_session
from flip.db.models.user_models import PermissionRef
from flip.utils.cognito_helpers import delete_cognito_user, get_username
from flip.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])

# TODO: Add a revoke token function to invalidate the user's token after deletion (figure if this should be here or in
# token/security/auth service)


# TODO [#114] This endpoint was not defined in the old repo, it was run as part of the 'registerUser' step function.
@router.delete("/{user_id}")
def delete_user(
    user_id: str,
    request: Request,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> dict[str, Any]:
    """
    Delete a user from the system.

    Args:
        user_id: ID of the user to delete
        request: FastAPI request object
        db: Database session
        token_id: ID of the authenticated user

    Returns:
        Empty response with 204 status code

    Raises:
        HTTPException: If the user doesn't have permission or other errors occur
    """
    try:
        # Check if user has permission to manage users
        if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
            logger.error(f"User with ID: {token_id} was unable to manage users")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {token_id} was unable to manage users"
            )

        # Get user pool ID and username
        user_pool_id = get_settings().AWS_COGNITO_USER_POOL_ID
        username = get_username(user_id, user_pool_id)

        if username:
            delete_cognito_user(username, user_pool_id)

        # Return empty response with 204 status code
        return {}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {str(e)}"
        )
