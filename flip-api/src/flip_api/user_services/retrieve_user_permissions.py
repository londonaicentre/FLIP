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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, col, select

from flip_api.auth.dependencies import verify_token
from flip.db.database import get_session
from flip.db.models.user_models import Permission, RolePermission, UserRole
from flip.domain.schemas.users import UserPermissionsResponse
from flip.utils.formatters import to_pascal_case
from flip.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])


def has_role(user_id: UUID, db: Session) -> bool:
    """Check if a user has at least one role assigned."""
    statement = select(UserRole).where(UserRole.user_id == user_id)
    user_role = db.exec(statement).first()
    return user_role is not None


def get_user_permissions(user_id: UUID, db: Session) -> List[Permission]:
    """Retrieve all permissions for a given user based on their roles."""
    # Get user roles
    user_roles = db.exec(select(UserRole).where(col(UserRole.user_id) == user_id)).all()

    # Get all permissions for these roles
    user_permissions: List[Permission] = []
    for user_role in user_roles:
        role_permissions = db.exec(select(RolePermission).where(col(RolePermission.role_id) == user_role.role_id)).all()
        user_permissions.extend(
            db.exec(select(Permission).where(col(Permission.id).in_([p.permission_id for p in role_permissions]))).all()
        )
    # Remove duplicates
    user_permissions = list({permission.id: permission for permission in user_permissions}.values())
    return user_permissions


# [#114] ✅
@router.get(
    "/{user_id}/permissions",
    response_model=UserPermissionsResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve User Permissions",
)
def retrieve_user_permissions(
    user_id: UUID,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> UserPermissionsResponse:
    """
    Retrieves the list of permissions associated with a specific user ID.

    - **user_id**: The unique identifier of the user whose permissions are to be retrieved.
    - **token_id**: The unique identifier of the authenticated user making the request (obtained from token).
    """
    # Verify the requesting user matches the user ID being queried
    # Note: This assumes users can only fetch their own permissions.
    # If admins should be able to fetch any user's permissions,
    # you'd add a permission check here (e.g., has_permissions(token_id, [Permission.CAN_MANAGE_USERS], db))
    # and potentially remove or adjust the verify_user check.
    # if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
    #     logger.error(f"User {token_id} attempted to access permissions for user {user_id}.")
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="User ID does not match token ID. Access denied.",
    #     )
    # TODO Review this. It would make sense to allow admins to fetch any user's permissions.
    if user_id != token_id:
        logger.error(f"User {token_id} attempted to access permissions for user {user_id}.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User ID does not match token ID. Access denied.",
        )

    try:
        # Check if the user exists and has roles assigned
        if not has_role(user_id, db):
            logger.warning(f"User {user_id} not found or has no roles assigned.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The user does not exist or does not have a role assigned.",
            )

        # Retrieve permissions
        permissions_list = get_user_permissions(user_id, db)
        logger.info(f"Successfully retrieved permissions for user {user_id}")

        return UserPermissionsResponse(permissions=[to_pascal_case(p.permission_name) for p in permissions_list])

    except HTTPException as http_exc:
        # Re-raise known HTTP exceptions
        raise http_exc
    except Exception as e:
        logger.exception(f"An unexpected error occurred while retrieving permissions for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred.",
        )
