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
from pydantic import ValidationError

from flip_api.auth.dependencies import verify_token
from flip_api.domain.schemas.users import GetUserByEmail, GetUserById
from flip_api.utils.cognito_helpers import get_user_by_email_or_id, get_user_pool_id
from flip_api.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])


# [#114] ✅
@router.get("/{user_id}")
def get_user(
    user_id: str,
    request: Request,
    token_id: UUID = Depends(verify_token),
):
    """
    Get user details by ID or email.

    Args:
        user_id: User ID or email
        request: FastAPI request object for headers

    Returns:
        User details
    """
    del token_id  # Unused variable
    try:
        # Try to validate as email first
        try:
            GetUserByEmail(userId=user_id)
            is_email = True
        except ValidationError:
            # If not email, try as UUID
            try:
                GetUserById(userId=user_id)
                is_email = False
            except ValidationError:
                # If neither, return error
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid user ID format. Must be a valid email or UUID.",
                )

        user_pool_id = get_user_pool_id(request)

        if is_email:
            user = get_user_by_email_or_id(user_pool_id, email=user_id)
        else:
            user = get_user_by_email_or_id(user_pool_id, user_id=UUID(user_id))

        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User '{user_id}' cannot be found.")

        return user

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {str(e)}"
        )
