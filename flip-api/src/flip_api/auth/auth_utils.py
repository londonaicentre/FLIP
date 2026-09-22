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

from flip_api.db.models.user_models import (
    TRUST_SCOPED_PERMISSIONS,
    PermissionRef,
    Role,
    RolePermission,
    UserRole,
)
from flip_api.utils.logger import logger


def _user_permission_ids(user_id: UUID, db: Session) -> set[UUID]:
    """
    Collect the IDs of every permission granted to a user through their GLOBAL roles.

    Only platform-wide grants count here (``user_role.trust_id IS NULL``). Trust-scoped
    grants are deliberately excluded: a Trust Owner's authority over their own trust must
    not leak into platform-wide checks. Use :func:`has_trust_permissions` for those.

    Raises rather than swallowing DB errors: each caller converts a failure into a deny, so the
    fail-closed behaviour stays visible at the point where the access decision is made.

    Args:
        user_id (UUID): The ID of the user to collect permissions for.
        db (Session): The database session to query user roles and permissions.

    Returns:
        set[UUID]: The permission IDs granted by the user's global roles.
    """
    # Get user roles
    user_roles = db.exec(
        select(Role).join(UserRole).where(UserRole.user_id == user_id).where(UserRole.trust_id.is_(None))  # type: ignore[union-attr]
    ).all()

    # Get all permissions for these roles
    user_permission_ids: set[UUID] = set()
    for role in user_roles:
        role_permissions = db.exec(select(RolePermission.permission_id).where(RolePermission.role_id == role.id)).all()
        user_permission_ids.update(role_permissions)

    return user_permission_ids


def _user_trust_permission_ids(user_id: UUID, trust_id: UUID, db: Session) -> set[UUID]:
    """
    Collect the permission IDs a user holds AT a specific trust.

    Matches only rows whose ``trust_id`` equals the given trust. Global roles
    (``trust_id IS NULL``) are not considered — see :func:`has_trust_permissions`.

    Args:
        user_id (UUID): The ID of the user to collect permissions for.
        trust_id (UUID): The trust the permissions must be held against.
        db (Session): The database session to query user roles and permissions.

    Returns:
        set[UUID]: The permission IDs granted by the user's roles at that trust.
    """
    user_roles = db.exec(
        select(Role).join(UserRole).where(UserRole.user_id == user_id).where(UserRole.trust_id == trust_id)
    ).all()

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

    An empty list grants nothing (returns False), matching :func:`has_any_permission`, so a caller
    cannot accidentally allow everyone by passing no permissions. That guard is explicit here
    because the natural implementation fails *open*: ``all()`` over an empty sequence is True, and
    the ``except`` below would not catch it — with nothing to check, the query succeeds and the
    generator never runs.

    Args:
        user_id (UUID): The ID of the user to check permissions for.
        required_permissions (list[PermissionRef]): A list of permissions to check against the user's roles.
        db (Session): The database session to query user roles and permissions.

    Returns:
        bool: True if the user has all required permissions, False otherwise
    """
    # Deny before touching the DB. A dynamically built list that filtered down to nothing is a
    # caller bug, so it is worth a log line rather than a silent deny.
    if not required_permissions:
        logger.error(f"Refusing to authorize user {user_id} against an empty required-permission list")
        return False

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


def has_trust_permissions(
    user_id: UUID,
    required_permissions: list[PermissionRef],
    trust_id: UUID,
    db: Session,
    *,
    require_trust_scoped: bool = True,
) -> bool:
    """
    Check if a user has ALL of the required permissions AT a specific trust.

    The trust-scoped counterpart to :func:`has_permissions`. Use it wherever the question is
    "may this user do X *at trust Y*" — approving a project for a trust, managing that trust's
    owners, editing its governance policy.

    **A global role does not satisfy this check.** Only ``user_role`` rows whose ``trust_id``
    matches are considered, so platform-wide Admin grants confer no authority over a trust's
    data. That is the whole point of the function, and it is load-bearing: ``db.seed.role_permissions``
    grants Admin *every* permission in :class:`PermissionRef`, so a check that accepted global
    grants would hand every hub administrator approval rights over every trust — the hole
    FLIP#1258 exists to close. The exclusion is enforced twice over, here and by keeping
    trust-scoped permissions out of the Admin seed (:data:`TRUST_SCOPED_PERMISSIONS`).

    An empty list grants nothing, matching :func:`has_permissions` — the same fail-open trap
    applies, since ``all()`` over an empty sequence is True.

    Args:
        user_id (UUID): The ID of the user to check permissions for.
        required_permissions (list[PermissionRef]): Permissions the user must hold at the trust.
        trust_id (UUID): The trust the permissions must be held against.
        db (Session): The database session to query user roles and permissions.
        require_trust_scoped (bool): Reject permissions that are not trust-scoped. Defaults to
            True so a caller cannot ask for a global permission (say ``CAN_MANAGE_USERS``) at a
            trust and have a Trust Owner grant satisfy it. Set False only when deliberately
            checking a dual-purpose permission at trust scope.

    Returns:
        bool: True if the user holds all required permissions at that trust, False otherwise.
    """
    # Deny before touching the DB, mirroring has_permissions.
    if not required_permissions:
        logger.error(f"Refusing to authorize user {user_id} at trust {trust_id} against an empty permission list")
        return False

    # A caller asking for a global permission at trust scope is a bug: it would let a Trust
    # Owner grant stand in for a platform-wide one. Deny loudly rather than answering it.
    if require_trust_scoped:
        non_scoped = [p.name for p in required_permissions if p.value not in TRUST_SCOPED_PERMISSIONS]
        if non_scoped:
            logger.error(
                f"Refusing to authorize user {user_id} at trust {trust_id}: "
                f"{sorted(non_scoped)} are not trust-scoped permissions"
            )
            return False

    try:
        user_permission_ids = _user_trust_permission_ids(user_id, trust_id, db)

        return all(permission.value in user_permission_ids for permission in required_permissions)

    except Exception as e:
        logger.error(f"Error checking trust permissions for user {user_id} at trust {trust_id}: {str(e)}")
        return False
