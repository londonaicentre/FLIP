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

from sqlmodel import Session, select

from flip_api.db.models.user_models import PermissionRef, Role, RolePermission, UserRole
from flip_api.utils.logger import logger


def has_permissions(user_id: UUID, required_permissions: list[PermissionRef], db: Session) -> bool:
    """
    Check if a user has the required permissions.

    Args:
        user_id (UUID): The ID of the user to check permissions for.
        required_permissions (list[PermissionRef]): A list of permissions to check against the user's roles.
        db (Session): The database session to query user roles and permissions.

    Returns:
        bool: True if the user has all required permissions, False otherwise
    """
    try:
        # Get user roles
        user_roles = db.exec(select(Role).join(UserRole).where(UserRole.user_id == user_id)).all()

        # Get all permissions for these roles
        user_permission_ids: list[UUID] = []
        for role in user_roles:
            role_permissions = db.exec(
                select(RolePermission.permission_id).where(RolePermission.role_id == role.id)
            ).all()
            user_permission_ids.extend(role_permissions)

        # Check if user has all required permissions
        return all(permission.value in user_permission_ids for permission in required_permissions)

    except Exception as e:
        logger.error(f"Error checking permissions: {str(e)}")
        return False
