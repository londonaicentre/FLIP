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

"""Database-backed user profile and role helpers.

Pure Postgres reads over ``user_profile``, ``roles`` and ``user_role``. Nothing here
touches AWS — these functions were split out of ``cognito_helpers`` so the Cognito
module names only code that actually talks to the identity provider.
"""

from collections import defaultdict
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from flip_api.db.models.user_models import Role, UserProfile, UserRole
from flip_api.domain.schemas.users import CognitoUser, IRole, IUser
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
    # Fetch roles for users
    user_ids = [user.id for user in users]
    statement = (
        select(col(UserRole.user_id), Role)
        .join(Role, col(Role.id) == col(UserRole.role_id))
        .where(col(UserRole.user_id).in_(user_ids))
    )
    role_results = session.exec(statement).all()
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

    # Filter by email and apply pagination
    search_str = paging_info.search_str.lower()
    filtered_users = [
        user
        for user in users
        if search_str in user.email.lower()
        or search_str in user_profiles_map.get(str(user.id), UserProfile(user_id=user.id)).name.lower()
        or search_str in user_profiles_map.get(str(user.id), UserProfile(user_id=user.id)).organisation.lower()
    ]
    sorted_users = sorted(filtered_users, key=lambda u: u.email)
    paged_users = sorted_users[paging_info.offset : paging_info.offset + paging_info.page_size]

    # Reconstruct IUser objects with roles
    final_users = [
        IUser(
            id=user.id,
            email=user.email,
            name=user_profiles_map.get(str(user.id), UserProfile(user_id=user.id)).name,
            organisation=user_profiles_map.get(str(user.id), UserProfile(user_id=user.id)).organisation,
            is_disabled=user.is_disabled,
            roles=user_roles_map.get(str(user.id), []),
        )  # type: ignore[call-arg]
        for user in paged_users
    ]

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
