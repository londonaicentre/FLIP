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

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.schemas.users import IUser
from flip_api.utils.cognito_helpers import get_cognito_users, get_pool_id, get_user_role_data
from flip_api.utils.logger import logger
from flip_api.utils.paging_utils import IPagedData, get_paging_details, get_total_pages

router = APIRouter(prefix="/users", tags=["user_services"])


# [#114] ✅
@router.get(
    "/",
    summary="Get Users",
    description="Get a list of users with pagination. Requires CAN_MANAGE_USERS permission.",
    response_model=IPagedData[IUser],
    status_code=status.HTTP_200_OK,
)
def get_users(
    request: Request,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> IPagedData[IUser]:
    """
    Get a list of users with pagination.

    Requires CAN_MANAGE_USERS permission

    Args:
        request (Request): FastAPI request object
        db (Session): Database session
        token_id (UUID): ID of the authenticated user

    Returns:
        IPagedData[IUser]: Paginated list of Cognito users

    Raises:
        HTTPException: If user does not have permission or if an error occurs
    """
    try:
        # Check if user has permission to manage users
        if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
            logger.error(f"User with ID: {token_id} was unable to manage users")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"User with ID: {token_id} was unable to manage users"
            )

        # Get user pool ID from request
        try:
            user_pool_id = get_pool_id(request)
        except Exception as e:
            logger.error(f"Failed to get user pool ID: {str(e)}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

        # Create paging info
        paging_info = get_paging_details(dict(request.query_params))

        # Get users from Cognito
        users = get_cognito_users(params={"UserPoolId": user_pool_id})

        data: List[IUser] = []

        # Get user role data if users exist
        if users:
            data = get_user_role_data(paging_info, users, db)
            # data = [row["data"] for row in rows]

        # Calculate pagination info
        total_records = len(data)
        total_pages = get_total_pages(total_records, paging_info.page_size)

        data_to_return: IPagedData[IUser] = IPagedData(
            page=paging_info.page_number,
            page_size=paging_info.page_size,
            total_pages=total_pages,
            total_records=total_records,
            data=data,
        )  # type: ignore[call-arg]

        logger.info(f"Returning user data: {data_to_return}")

        return data_to_return

    except HTTPException:
        # Re-raise HTTP exceptions
        raise

    except Exception as e:
        logger.error(f"Unhandled error in get_users: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
