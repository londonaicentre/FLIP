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

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Sequence
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import boto3
from botocore.exceptions import ClientError
from fastapi import HTTPException, Request, status
from sqlmodel import Session, col, select

from flip_api.config import get_settings
from flip_api.db.models.user_models import Role, UserRole
from flip_api.domain.schemas.users import CognitoUser, Disabled, IRole, IUser
from flip_api.utils.logger import logger
from flip_api.utils.paging_utils import PagingInfo

boto3.set_stream_logger("boto3.resources", logging.INFO)


@lru_cache(maxsize=1)
def _cognito_client() -> Any:
    """Module-level cached cognito-idp client. boto3 clients are thread-safe
    and expensive to construct; sharing one avoids paying endpoint-resolution
    cost on every authenticated request."""
    return boto3.client("cognito-idp", region_name=get_settings().AWS_REGION)


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin_from_url(url: str) -> str | None:
    """Return ``scheme://host[:port]`` for ``url``, omitting ports that match the scheme default.

    Browsers strip default ports from the ``Origin`` header (RFC 6454), so an allowlist entry
    like ``https://localhost:443`` would never match an actual request — normalize before use.
    Returns ``None`` for URLs without a usable scheme/host.
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        return None
    host = parsed.hostname
    port = parsed.port
    if port is None or port == _DEFAULT_PORTS.get(parsed.scheme):
        return f"{parsed.scheme}://{host}"
    return f"{parsed.scheme}://{host}:{port}"


def get_cors_allowed_origins() -> list[str]:
    """Derive the CORS allowlist from the Cognito user pool client's CallbackURLs.

    The same Cognito app client that authenticates UI logins already enumerates the trusted UI
    origins per environment (see ``deploy/providers/AWS/services.tf``). Reusing it as the CORS
    allowlist keeps "where users can sign in" and "where the UI may call this API" in lockstep,
    without a separate env var.

    Returns:
        list[str]: Unique normalized origins (``scheme://host[:port]``) suitable for
        ``CORSMiddleware(allow_origins=...)``.
    """
    settings = get_settings()
    response = _cognito_client().describe_user_pool_client(
        UserPoolId=settings.AWS_COGNITO_USER_POOL_ID,
        ClientId=settings.AWS_COGNITO_APP_CLIENT_ID,
    )
    callback_urls: list[str] = response.get("UserPoolClient", {}).get("CallbackURLs", []) or []

    seen: set[str] = set()
    origins: list[str] = []
    for url in callback_urls:
        origin = _origin_from_url(url)
        if origin and origin not in seen:
            seen.add(origin)
            origins.append(origin)
    return origins


def get_pool_id(request: Request) -> str:
    """
    Extract the user pool ID from the request context.

    Args:
        request (Request): FastAPI request object

    Returns:
        str: The user pool ID extracted from the request context

    Raises:
        HTTPException: If the user pool ID is not found
    """
    logger.debug("Attempting to get the userPoolId...")

    # First try from environment variable (for local development/testing)
    user_pool_id = get_settings().AWS_COGNITO_USER_POOL_ID

    # If not in environment, try to get from request auth context
    if not user_pool_id and hasattr(request.state, "auth"):
        # This assumes JWT claims are stored in request.state.auth.claims
        claims = getattr(request.state.auth, "claims", {})
        if claims and "iss" in claims:
            user_pool_id = claims["iss"].split("amazonaws.com/")[-1]

    if not user_pool_id:
        logger.error("Token does not contain userPoolId")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Token does not contain userPoolId"
        )

    logger.info(f"UserPoolId: {user_pool_id}")
    return user_pool_id


def get_user_pool_id(request: Request) -> str:
    # FIXME replace occurrences of get_pool_id with this function
    return get_pool_id(request)


def get_cognito_users(params: dict[str, Any] | None = None) -> list[CognitoUser]:
    """
    Get users from Cognito user pool.

    Args:
        params (dict[str, Any] | None): Additional parameters to pass to the ListUsers API call.

    Returns:
        list[CognitoUser]: List of CognitoUser objects.

    Raises:
        HTTPException: If there is an error fetching users from Cognito or if the user pool ID is not found.
    """
    user_pool_id = get_settings().AWS_COGNITO_USER_POOL_ID
    client = _cognito_client()
    if params is None:
        params = {"UserPoolId": user_pool_id}
    elif "UserPoolId" not in params:
        params["UserPoolId"] = user_pool_id

    try:
        logger.debug(f"Cognito list users params: {params}")
        response = client.list_users(**params)

        cognito_users = response.get("Users", [])
        users: list[CognitoUser] = []

        for user in cognito_users:
            attributes = {attr["Name"]: attr["Value"] for attr in user.get("Attributes", [])}
            user_id = attributes.get("sub", "")
            email = attributes.get("email", user.get("Username", ""))
            is_disabled = not user.get("Enabled", True)

            users.append(
                CognitoUser(
                    id=UUID(user_id),
                    email=email,
                    is_disabled=is_disabled,
                )  # type: ignore[call-arg]
            )

        return users
    except ClientError as e:
        # boto3 ClientError messages can carry request IDs and ARNs that
        # don't belong in a 500 detail surfaced to API clients. Log the
        # full error server-side, return a generic message.
        logger.exception("Error getting Cognito users")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get Cognito users"
        ) from e


def get_user_by_email_or_id(
    user_pool_id: str, email: str | None = None, user_id: UUID | None = None
) -> CognitoUser:
    """
    Get a user from Cognito by email or ID.

    Args:
        user_pool_id (str): Cognito user pool ID
        email (str | None): User email (optional)
        user_id (UUID | None): User ID (optional)

    Returns:
        CognitoUser: The user matching the email or ID.

    Raises:
        HTTPException: If neither email nor user_id is provided
    """
    if not email and not user_id:
        logger.error("No user email address or ID provided")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No user email address or ID provided")

    filter_expr = f'email = "{email}"' if email else f'sub = "{user_id}"'

    params = {"UserPoolId": user_pool_id, "Filter": filter_expr, "Limit": 1}

    logger.debug(f"Cognito filter params: {params}")

    try:
        users: Sequence[CognitoUser] = get_cognito_users(params)
    except HTTPException:
        # `get_cognito_users` already raised a sanitised 500; preserve it
        # rather than re-wrapping (which would echo the inner detail back
        # through `str(e)` and risk leaking Cognito internals).
        logger.exception("SKIPPING COGNITO USER LISTING")
        raise

    logger.debug(f"Found users: {users}")

    if not users:
        logger.warning(f"No user found with email: {email} or ID: {user_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with email: {email} or ID: {user_id} is not registered.",
        )

    return users[0]


def get_username(user_id: str, user_pool_id: str) -> str:
    """
    Get a username from Cognito by user ID.

    Args:
        user_id (str): User ID (sub in Cognito)
        user_pool_id (str): Cognito user pool ID

    Returns:
        str: The username (email) associated with the user ID.

    Raises:
        HTTPException: 404 if no matching user, 500 on Cognito errors.
    """
    params = {
        "UserPoolId": user_pool_id,
        "Filter": f'sub="{user_id}"',
    }

    users = get_cognito_users(params)

    logger.debug(f"Found users: {users}")

    if not users:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User with ID {user_id} is not registered.")
    if len(users) > 1:
        logger.warning(f"Multiple users found for ID {user_id}, returning the first one")
    logger.debug(f"Returning username for user ID {user_id}: {users[0].email}")
    return users[0].email


def update_user(username: str, user_pool_id: str, disabled: bool) -> Disabled:
    """
    Enable or disable a user in Cognito.

    Args:
        username (str): Username (email)
        user_pool_id (str): Cognito user pool ID
        disabled (bool): Whether to disable the user

    Returns:
        Disabled: An object indicating the disabled status of the user after the update.

    Raises:
        HTTPException: If the request cannot be processed.
    """
    client = _cognito_client()

    try:
        params = {"UserPoolId": user_pool_id, "Username": username}

        if disabled:
            client.admin_disable_user(**params)
        else:
            client.admin_enable_user(**params)

        logger.debug(f"User {username} {'disabled' if disabled else 'enabled'}")

        return Disabled(disabled=disabled)
    except ClientError as e:
        logger.exception("Error updating user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update user"
        ) from e


def delete_cognito_user(username: str, user_pool_id: str) -> None:
    """
    Delete a user from Cognito.

    Args:
        username (str): Username (email)
        user_pool_id (str): Cognito user pool ID

    Returns:
        None

    Raises:
        HTTPException: If there is an error deleting the user from Cognito.
    """
    logger.debug(f"Attempting to delete user: {username}")

    client = _cognito_client()

    try:
        client.admin_delete_user(UserPoolId=user_pool_id, Username=username)

        logger.info(f"Successfully deleted user: {username}")
    except ClientError as e:
        logger.exception("Error deleting user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete user"
        ) from e


def reset_user_mfa(username: str, user_pool_id: str) -> None:
    """
    Disable a user's TOTP MFA preference and invalidate their sessions.

    Cognito has no admin API to delete a verified TOTP secret, so clearing
    the preference is the only server-side handle; the app-layer MFA gate
    (``verify_token`` + router guard) then funnels the user through
    post-auth enrolment, which mints a fresh secret. A global sign-out
    revokes any active refresh tokens so a pre-reset session cannot keep
    operating.

    Args:
        username (str): Username (email)
        user_pool_id (str): Cognito user pool ID

    Returns:
        None

    Raises:
        HTTPException: If resetting MFA or signing the user out fails
    """
    logger.debug(f"Attempting to reset MFA for user: {username}")

    client = _cognito_client()

    try:
        client.admin_set_user_mfa_preference(
            UserPoolId=user_pool_id,
            Username=username,
            SoftwareTokenMfaSettings={"Enabled": False, "PreferredMfa": False},
        )
        client.admin_user_global_sign_out(UserPoolId=user_pool_id, Username=username)
        _invalidate_mfa_cache(username, user_pool_id)

        logger.info(f"Successfully reset MFA and revoked sessions for user: {username}")
    except ClientError as e:
        logger.exception("Error resetting user MFA")
        # Generic client-facing detail: the boto3 `str(e)` can include
        # Cognito error codes, request IDs, or ARNs. Do not echo it.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset user MFA",
        ) from e


# TTL cache for is_mfa_enabled. verify_token calls this on every
# authenticated request, so a short-lived in-process cache sidesteps the
# Cognito AdminGetUser round-trip and its throttling ceiling. The cache is
# invalidated from reset_user_mfa so admin resets take effect immediately;
# the TTL is the upper bound on staleness when a user's MFA preference
# changes through a path that doesn't go through reset_user_mfa (e.g. the
# user enrolling via updateMFAPreference on the client).
_MFA_STATE_TTL_SECONDS = 60.0
_mfa_state_cache: dict[tuple[str, str], tuple[bool, float]] = {}
_mfa_state_cache_lock = threading.Lock()


def _invalidate_mfa_cache(username: str, user_pool_id: str) -> None:
    """Drop the cached MFA-enabled state for a user (used after admin reset)."""
    with _mfa_state_cache_lock:
        _mfa_state_cache.pop((user_pool_id, username), None)


def is_mfa_enabled(username: str, user_pool_id: str) -> bool:
    """
    Check whether a user has TOTP MFA active in Cognito.

    A user is considered MFA-active if SOFTWARE_TOKEN_MFA is present in
    their UserMFASettingList — Cognito only adds that entry after the
    user has both verified a software token and had their preference set
    with Enabled=True.

    Results are cached for a short TTL because ``verify_token`` calls this
    on every authenticated request; see ``_MFA_STATE_TTL_SECONDS``.

    Args:
        username (str): Username (email)
        user_pool_id (str): Cognito user pool ID

    Returns:
        bool: True if TOTP MFA is enabled for the user, False otherwise.

    Raises:
        HTTPException: If the Cognito lookup fails.
    """
    cache_key = (user_pool_id, username)
    now = time.monotonic()
    with _mfa_state_cache_lock:
        cached = _mfa_state_cache.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]

    client = _cognito_client()

    try:
        response = client.admin_get_user(UserPoolId=user_pool_id, Username=username)
    except ClientError as e:
        logger.exception(f"Error fetching MFA state for user {username}")
        # Hot path: called by verify_token on every authenticated request.
        # Keep the detail generic to avoid echoing Cognito/boto3 internals.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch MFA state",
        ) from e

    enabled = "SOFTWARE_TOKEN_MFA" in response.get("UserMFASettingList", [])
    with _mfa_state_cache_lock:
        _mfa_state_cache[cache_key] = (enabled, time.monotonic() + _MFA_STATE_TTL_SECONDS)
    return enabled


def revoke_token(refresh_token: str, client_id: str) -> None:
    """
    Revoke a refresh token in Cognito.

    Args:
        refresh_token (str): Refresh token to revoke
        client_id (str): Cognito app client ID

    Returns:
        None

    Raises:
        HTTPException: If token revocation fails
    """
    client = _cognito_client()

    try:
        client.revoke_token(Token=refresh_token, ClientId=client_id)

        logger.info("Successfully revoked refresh token")
    except ClientError as e:
        logger.exception("Error revoking token")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revoke token"
        ) from e


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
    user_ids = [str(user.id) for user in users]
    statement = (
        select(col(UserRole.user_id), Role)
        .join(Role, col(Role.id) == col(UserRole.role_id))
        .where(col(UserRole.user_id).in_(user_ids))
    )
    role_results = session.exec(statement).all()

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
    filtered_users = [user for user in users if paging_info.search_str.lower() in user.email.lower()]
    sorted_users = sorted(filtered_users, key=lambda u: u.email)
    paged_users = sorted_users[paging_info.offset : paging_info.offset + paging_info.page_size]

    # Reconstruct IUser objects with roles
    final_users = [
        IUser(
            id=user.id,
            email=user.email,
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


def create_cognito_user(email: str, user_pool_id: str) -> UUID:
    """
    Create a new user in Cognito.

    Args:
        email (str): User email
        user_pool_id (str): Cognito user pool ID

    Returns:
        UUID: The ID of the created user

    Raises:
        HTTPException: If user creation fails
    """
    logger.debug("Attempting to register the user...")

    client = _cognito_client()

    try:
        response = client.admin_create_user(
            UserPoolId=user_pool_id,
            Username=email,
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
        )

        logger.debug(f"Response from create user request: {response}")

        # Extract user ID (sub) from attributes
        user_id = next((attr["Value"] for attr in response["User"]["Attributes"] if attr["Name"] == "sub"), None)

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User created but could not get user ID"
            )

        logger.info("User has been created successfully")

        return user_id

    except ClientError as e:
        if e.response["Error"]["Code"] == "UsernameExistsException":
            logger.error(f"User with email {email} already exists")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"User with email {email} already exists"
            ) from e
        else:
            logger.exception("Error creating user")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create user"
            ) from e


def filter_enabled_users(user_pool_id: str, users: list[UUID]) -> list[UUID]:
    """
    Filter out disabled users from a list of user IDs.

    Args:
        user_pool_id (str): Cognito user pool ID
        users (list[UUID]): List of user IDs to filter

    Returns:
        list[UUID]: List of enabled user IDs
    """
    if not users:
        return []

    cognito_users = get_cognito_users(params={"UserPoolId": user_pool_id})

    # Create a map of user IDs to users
    user_map = {user.id: user for user in cognito_users}

    valid_users = []
    invalid_users = []

    for user_id in users:
        if user_id in user_map and not user_map[user_id].is_disabled:
            valid_users.append(user_id)
        else:
            invalid_users.append(user_id)

    if invalid_users:
        for user_id in invalid_users:
            logger.warning(f"User {user_id} is either disabled or does not exist")

    return valid_users
