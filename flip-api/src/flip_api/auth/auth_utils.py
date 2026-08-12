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


def _user_permission_ids(user_id: UUID, db: Session) -> set[UUID]:
    """
    Collect the IDs of every permission granted to a user through their roles.

    Raises rather than swallowing DB errors: each caller converts a failure into a deny, so the
    fail-closed behaviour stays visible at the point where the access decision is made.

    Args:
        user_id (UUID): The ID of the user to collect permissions for.
        db (Session): The database session to query user roles and permissions.

    Returns:
        set[UUID]: The permission IDs granted by the user's roles.
    """
    # Get user roles
    user_roles = db.exec(select(Role).join(UserRole).where(UserRole.user_id == user_id)).all()

    # Get all permissions for these roles
    user_permission_ids: set[UUID] = set()
    for role in user_roles:
        role_permissions = db.exec(select(RolePermission.permission_id).where(RolePermission.role_id == role.id)).all()
        user_permission_ids.update(role_permissions)

    return user_permission_ids


def has_permissions(user_id: UUID, required_permissions: list[PermissionRef], db: Session) -> bool:
    """
    Check if a user has ALL of the required permissions.

    This is an AND check — ``has_permissions(uid, [A, B], db)`` is True only when the user holds
    both A and B. For an OR check, use :func:`has_any_permission`; passing two permissions here
    when either would do silently denies everyone who holds just one of them.

    Args:
        user_id (UUID): The ID of the user to check permissions for.
        required_permissions (list[PermissionRef]): A list of permissions to check against the user's roles.
        db (Session): The database session to query user roles and permissions.

    Returns:
        bool: True if the user has all required permissions, False otherwise
    """
    try:
        user_permission_ids = _user_permission_ids(user_id, db)

        # Check if user has all required permissions
        return all(permission.value in user_permission_ids for permission in required_permissions)

    except Exception as e:
        logger.error(f"Error checking all-of permissions for user {user_id}: {str(e)}")
        return False


def has_any_permission(user_id: UUID, permissions: list[PermissionRef], db: Session) -> bool:
    """
    Check if a user has AT LEAST ONE of the given permissions.

    The OR counterpart to :func:`has_permissions`, which is an AND check. An empty list grants
    nothing (returns False), so a caller cannot accidentally allow everyone by passing no
    permissions.

    Args:
        user_id (UUID): The ID of the user to check permissions for.
        permissions (list[PermissionRef]): The permissions to check against the user's roles.
        db (Session): The database session to query user roles and permissions.

    Returns:
        bool: True if the user holds any one of the given permissions, False otherwise.
    """
    try:
        user_permission_ids = _user_permission_ids(user_id, db)

        return any(permission.value in user_permission_ids for permission in permissions)

    except Exception as e:
        logger.error(f"Error checking any-of permissions for user {user_id}: {str(e)}")
        return False
