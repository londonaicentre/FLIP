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
from sqlmodel import Session, col, delete, select

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef, User, UserRole, UsersAudit
from flip_api.domain.interfaces.user import IRoles
from flip_api.utils.cognito_helpers import validate_roles
from flip_api.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])


@router.post("/{user_id}/roles", response_model=IRoles)
def set_user_roles(
    user_id: UUID,
    roles_data: IRoles,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> IRoles:
    """
    Set roles for a user.

    Args:
        user_id (UUID): The ID of the user to update roles for.
        roles_data (IRoles): The roles data containing a list of role IDs to assign to the user.
        db (Session): The database session.
        token_id (UUID): The ID of the user making the request, used for permission checks.

    Returns:
        IRoles: The updated roles data for the user.

    Raises:
        HTTPException: If the user does not have permission to update roles or if any role does not exist.
    """
    try:
        # Check permissions
        if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
            logger.error(f"User with ID: {token_id} attempted to update roles without permission")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User with ID: {token_id} was unable to update a user's roles",
            )

        user_roles_ids = roles_data.roles

        # Get all available roles for validation
        role_ids_from_db = db.exec(select(UserRole.role_id).distinct()).all()
        role_ids: List[UUID] = [r for r in role_ids_from_db if r is not None]
        validate_roles(user_roles_ids, role_ids)  # This will raise an HTTPException if any role is invalid

        # Delete existing roles
        delete_stmt = delete(UserRole).where(col(UserRole.user_id) == user_id)
        deleted = db.execute(delete_stmt)
        db.commit()

        if hasattr(deleted, "rowcount") and deleted.rowcount == 0:
            logger.info("No changes made to the database. The user did not have any roles.")

        # List current users in the database
        existing_users = db.exec(select(User)).all()
        logger.info(f"Current users in the database: {[user.id for user in existing_users]}")

        # Insert new roles
        logger.info(f"Setting roles for user {user_id}: {user_roles_ids}")
        new_user_roles = [UserRole(user_id=user_id, role_id=role_id) for role_id in user_roles_ids]
        db.add_all(new_user_roles)
        db.commit()

        if len(new_user_roles) != len(user_roles_ids):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to set all roles. Please check the provided role IDs.",
            )

        # Add audit record
        audit = UsersAudit(
            action=f"Updated roles: [{user_roles_ids}]",
            user_id=user_id,
            modified_by_user_id=token_id,
        )
        db.add(audit)
        db.commit()

        return roles_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting user roles: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {str(e)}"
        )
