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

"""User profile and role helpers.

Postgres reads over ``user_profile``, ``roles`` and ``user_role``, plus the in-memory
``validate_roles`` guard that callers apply to what those reads return (it takes the
role IDs as an argument and queries nothing itself).

Nothing here touches AWS: no ``boto3`` import, and every transitive import is AWS-free.
Split out of ``cognito_helpers`` so that module stays about the Cognito user directory.
"""

from collections import defaultdict
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from flip_api.db.models.main_models import Trust
from flip_api.db.models.user_models import Role, RoleRef, UserProfile, UserRole
from flip_api.domain.schemas.users import CognitoUser, IRole, ITrustAdminOf, IUser
from flip_api.utils.logger import logger
from flip_api.utils.paging_utils import PagingInfo


def apply_user_profile(user: CognitoUser, session: Session) -> CognitoUser:
    """Attach DB-backed profile fields to a Cognito user response."""
    if not hasattr(session, "get"):
        return user

    profile = session.get(UserProfile, user.id)
    if not profile:
        return user

    return CognitoUser(
        id=user.id,
        email=user.email,
        name=profile.name,
        organisation=profile.organisation,
        is_disabled=user.is_disabled,
    )  # type: ignore[call-arg]


def get_user_role_data(
    paging_info: PagingInfo,
    users: list[CognitoUser],
    session: Session,
) -> list[IUser]:
    """
    Get user role data with pagination and filtering.

    Args:
        paging_info (PagingInfo): Pagination and filtering information.
        users (list[CognitoUser]): List of Cognito users.
        session (Session): Database session.

    Returns:
        list[IUser]: List of IUser objects with roles.
    """
    # Fetch roles for users. Global grants only (FLIP#1260): this feeds the admin user
    # list, which shows platform roles. A Trust Admin (FLIP#1258) — a global Researcher row
    # plus a Trust Admin row at one trust — is shown as that one role, with its trust.
    user_ids = [user.id for user in users]
    statement = (
        select(col(UserRole.user_id), Role)
        .join(Role, col(Role.id) == col(UserRole.role_id))
        .where(col(UserRole.user_id).in_(user_ids))
        .where(col(UserRole.trust_id).is_(None))
    )
    role_results = session.exec(statement).all()
    admin_rows = session.exec(
        select(col(UserRole.user_id), Role, Trust)
        .join(Role, col(Role.id) == col(UserRole.role_id))
        .join(Trust, col(Trust.id) == col(UserRole.trust_id))
        .where(col(UserRole.user_id).in_(user_ids))
        .where(col(UserRole.role_id) == RoleRef.TRUST_ADMIN.value)
    ).all()
    trust_admin_map = {
        str(uid): (
            IRole(id=role.id, rolename=role.name, roledescription=role.description),
            ITrustAdminOf(id=trust.id, code=trust.code, name=trust.name),
        )
        for uid, role, trust in admin_rows
    }
    profiles = session.exec(select(UserProfile).where(col(UserProfile.user_id).in_(user_ids))).all()
    user_profiles_map = {str(profile.user_id): profile for profile in profiles}

    # Group roles by user_id
    user_roles_map: dict[str, list[IRole]] = defaultdict(list)
    for user_id, role in role_results:
        if role and role.id is not None:
            user_roles_map[str(user_id)].append(
                IRole(
                    id=role.id,
                    rolename=role.name,
                    roledescription=role.description,
                )
            )

    def profile_of(user: CognitoUser) -> UserProfile:
        return user_profiles_map.get(str(user.id), UserProfile(user_id=user.id))

    # Filter by email/name/organisation and apply pagination
    search_str = paging_info.search_str.lower()

    def matches(user: CognitoUser) -> bool:
        profile = profile_of(user)
        return any(search_str in t.lower() for t in (user.email, profile.name, profile.organisation))

    filtered_users = [user for user in users if matches(user)]
    sorted_users = sorted(filtered_users, key=lambda u: u.email)
    paged_users = sorted_users[paging_info.offset : paging_info.offset + paging_info.page_size]

    # Reconstruct IUser objects with roles; a trust admin reports that one role and its trust
    final_users: list[IUser] = []
    for user in paged_users:
        profile = profile_of(user)
        admin = trust_admin_map.get(str(user.id))
        final_users.append(
            IUser(
                id=user.id,
                email=user.email,
                name=profile.name,
                organisation=profile.organisation,
                is_disabled=user.is_disabled,
                roles=[admin[0]] if admin else user_roles_map.get(str(user.id), []),
                trust_admin_of=admin[1] if admin else None,
            )  # type: ignore[call-arg]
        )

    return final_users


def get_all_roles(db: Session) -> list[UUID]:
    """
    Get all role IDs from the database.

    Args:
        db (Session): Database session

    Returns:
        list[UUID]: List of role IDs
    """
    logger.debug("Attempting to get the list of roles from the database...")

    result = db.exec(select(Role.id)).all()

    role_ids = [role_id for role_id in result]

    logger.info(f"Found {len(role_ids)} roles: {role_ids}")

    return role_ids


def validate_roles(user_roles: list[UUID], roles_from_db: list[UUID]) -> None:
    """
    Validate that all user roles exist in the database.

    Args:
        user_roles (list[UUID]): List of role IDs to validate
        roles_from_db (list[UUID]): List of valid role IDs from the database

    Returns:
        None

    Raises:
        HTTPException: If any role is invalid
    """
    logger.debug(f"Attempting to validate user roles: {user_roles}")

    invalid_roles = [role for role in user_roles if role not in roles_from_db]

    if invalid_roles:
        logger.error(f"Invalid role(s): {invalid_roles}. They do not match the roles in the database: {roles_from_db}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid role(s): {invalid_roles}")
