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
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence
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


def get_pool_id(request: Request) -> str:
    """
    Extract the user pool ID from the request context.

    Args:
        request: FastAPI request object

    Returns:
        User pool ID string

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


def get_cognito_users(params: Optional[Dict[str, Any]] = None) -> List[CognitoUser]:
    """
    Get users from Cognito user pool.

    Args:
        params (Optional[Dict[str, Any]]): Additional parameters to pass to the ListUsers API call.

    Returns:
        List[CognitoUser]: List of CognitoUser objects.
    """
    user_pool_id = get_settings().AWS_COGNITO_USER_POOL_ID
    client = boto3.client("cognito-idp", region_name=get_settings().AWS_REGION)
    if params is None:
        params = {"UserPoolId": user_pool_id}
    elif "UserPoolId" not in params:
        params["UserPoolId"] = user_pool_id

    try:
        logger.debug(f"Cognito list users params: {params}")
        response = client.list_users(**params)

        cognito_users = response.get("Users", [])
        users: List[CognitoUser] = []

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
        logger.error(f"Error getting Cognito users: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get Cognito users: {str(e)}"
        )


def get_user_by_email_or_id(
    user_pool_id: str, email: Optional[str] = None, user_id: Optional[UUID] = None
) -> CognitoUser:
    """
    Get a user from Cognito by email or ID.

    Args:
        user_pool_id: Cognito user pool ID
        email: User email (optional)
        user_id: User ID (optional)

    Returns:
        CognitoUser object or None if not found

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
    except HTTPException as e:
        logger.error(f"SKIPPING COGNITO USER LISTING: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get Cognito users: {str(e)}"
        )

    logger.debug(f"Found users: {users}")

    if not users:
        logger.warning(f"No user found with email: {email} or ID: {user_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with email: {email} or ID: {user_id} is not registered.",
        )

    return users[0]


def get_username(user_id: str, user_pool_id: str) -> Optional[str]:
    """
    Get a username from Cognito by user ID.

    Args:
        user_id: User ID
        user_pool_id: Cognito user pool ID

    Returns:
        Username (email) or empty string if not found
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
        username: Username (email)
        user_pool_id: Cognito user pool ID
        disabled: Whether to disable the user

    Returns:
        Disabled object with the updated disabled status
    """
    client = boto3.client("cognito-idp", region_name=get_settings().AWS_REGION)

    try:
        params = {"UserPoolId": user_pool_id, "Username": username}

        if disabled:
            client.admin_disable_user(**params)
        else:
            client.admin_enable_user(**params)

        logger.debug(f"User {username} {'disabled' if disabled else 'enabled'}")

        return Disabled(disabled=disabled)
    except ClientError as e:
        logger.error(f"Error updating user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update user: {str(e)}"
        )


def delete_cognito_user(username: str, user_pool_id: str) -> None:
    """
    Delete a user from Cognito.

    Args:
        username: Username (email)
        user_pool_id: Cognito user pool ID

    Raises:
        HTTPException: If deletion fails
    """
    logger.debug(f"Attempting to delete user: {username}")

    client = boto3.client("cognito-idp", region_name=get_settings().AWS_REGION)

    try:
        client.admin_delete_user(UserPoolId=user_pool_id, Username=username)

        logger.info(f"Successfully deleted user: {username}")
    except ClientError as e:
        logger.error(f"Error deleting user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete user: {str(e)}"
        )


def revoke_token(refresh_token: str, client_id: str) -> None:
    """
    Revoke a refresh token in Cognito.

    Args:
        refresh_token: Refresh token to revoke
        client_id: Cognito app client ID

    Raises:
        HTTPException: If token revocation fails
    """
    client = boto3.client("cognito-idp", region_name=get_settings().AWS_REGION)

    try:
        client.revoke_token(Token=refresh_token, ClientId=client_id)

        logger.info("Successfully revoked refresh token")
    except ClientError as e:
        logger.error(f"Error revoking token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to revoke token: {str(e)}"
        )


def get_user_role_data(
    paging_info: PagingInfo,
    users: List[CognitoUser],
    session: Session,
) -> List[IUser]:
    """
    Get user role data with pagination and filtering.

    Args:
        paging_info (PagingInfo): Pagination and filtering information.
        users (List[CognitoUser]): List of Cognito users.
        session (Session): Database session.

    Returns:
        List[IUser]: List of IUser objects with roles.
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
    user_roles_map: Dict[str, List[IRole]] = defaultdict(list)
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


def get_all_roles(db: Session) -> List[UUID]:
    """
    Get all role IDs from the database.

    Args:
        db: Database session

    Returns:
        List of role IDs
    """
    logger.debug("Attempting to get the list of roles from the database...")

    result = db.exec(select(Role.id)).all()

    role_ids = [role_id for role_id in result]

    logger.info(f"Found {len(role_ids)} roles: {role_ids}")

    return role_ids


def validate_roles(user_roles: List[UUID], roles_from_db: List[UUID]) -> None:
    """
    Validate that all user roles exist in the database.

    Args:
        user_roles: List of role IDs to validate
        roles_from_db: List of valid role IDs from the database

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
        email: User email
        user_pool_id: Cognito user pool ID

    Returns:
        User ID (sub)

    Raises:
        HTTPException: If user creation fails
    """
    logger.debug("Attempting to register the user...")

    client = boto3.client("cognito-idp", region_name=get_settings().AWS_REGION)

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
            )
        else:
            logger.error(f"Error creating user: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create user: {str(e)}"
            )


def filter_enabled_users(user_pool_id: str, users: List[UUID]) -> List[UUID]:
    """
    Filter out disabled users from a list of user IDs.

    Args:
        user_pool_id: Cognito user pool ID
        users: List of user IDs to filter

    Returns:
        List of enabled user IDs
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
