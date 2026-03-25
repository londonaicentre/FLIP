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

import re

import requests

from imaging_api.config import get_settings
from imaging_api.routers.schemas import CentralHubUser, CreatedUser, CreateUser, User
from imaging_api.utils.encryption import encrypt
from imaging_api.utils.exceptions import AlreadyExistsError, NotFoundError
from imaging_api.utils.logger import logger
from imaging_api.utils.passwords import generate_complex_password

XNAT_URL = get_settings().XNAT_URL


def get_xnat_users(headers: dict[str, str]) -> list[User]:
    """
    Gets all users from XNAT.

    Args:
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        List[imaging_api.routers.schemas.User]: List of XNAT users.
    """
    response = requests.get(f"{XNAT_URL}/xapi/users/profiles", headers=headers)
    users = [User(**user) for user in response.json()]

    if response.status_code == 200:
        return users
    else:
        raise Exception(f"Error: Getting XNAT users failed: {response.status_code} - {response.text}")


def to_create_imaging_user(user: CentralHubUser, headers: dict[str, str]) -> CreateUser:
    """
    Converts central hub user (mainly email) to an XNAT CreateUser object.

    XNAT usernames are unique, so we have to create a unique XNAT username from the email address.
    We first grab the part of the email before '@'.
    If the username already exists, we append a number suffix to make it unique.

    Args:
        user (imaging_api.routers.schemas.CentralHubUser): The user's details on the Central Hub.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        imaging_api.routers.schemas.CreateUser: XNAT user creation request object.
    """
    # Extract username from email (part before @)
    base_username = user.email.split("@")[0]

    # Replace all non-alphanumeric characters as they're not supported
    username = re.sub(r"[^a-zA-Z0-9 \-]", "", base_username)

    # Get existing users to check for uniqueness
    try:
        existing_users = get_xnat_users(headers)
    except Exception as e:
        raise Exception(f"Error: XNAT error when fetching users: {str(e)}")

    # Keep adding a suffix until we find a unique username
    if existing_users:
        existing_usernames = {u.username for u in existing_users}
        suffix = 1
        while username in existing_usernames:
            username = f"{base_username}{suffix}"
            suffix += 1

    # Create and return the user profile
    return CreateUser(
        username=username,
        password=generate_complex_password(),
        firstName=username,
        lastName=username,
        email=user.email,
        enabled=not user.is_disabled,
    )


def get_user_profile_by(key: str, value: str, headers: dict[str, str]) -> User:
    """
    Fetches a user profile from XNAT using the key and value.

    Args:
        key (str): The key to search by. Can be 'username' or 'email'.
        value (str): The value to search for.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        imaging_api.routers.schemas.User: The user profile.
    """
    assert key in [
        "username",
        "email",
    ], f"Invalid key: {key}, must be 'username' or 'email'"
    try:
        users = get_xnat_users(headers)
    except Exception as e:
        raise Exception(f"Error: XNAT error when fetching users: {str(e)}")

    for user in users:
        if getattr(user, key) == value:
            return user

    raise NotFoundError(f"User not found by {key}")


def user_exists(username: str, headers: dict[str, str]) -> bool:
    """
    Checks if a user exists in XNAT.

    In XNAT, username is unique, so it can be used to identify the user:
    `"ERROR: The username {username} is already in use. Please enter a different username value and try again."`

    Args:
        username (str): The username to check.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        bool: True if the user exists, False otherwise.
    """
    try:
        get_user_profile_by("username", username, headers)
        return True
    except NotFoundError:
        return False


def create_user_from_central_hub_user(
    central_hub_user: CentralHubUser, headers: dict[str, str]
) -> tuple[CreatedUser, User]:
    """
    Convert central hub user to XNAT CreateUser request object, and create user on XNAT.

    Args:
        central_hub_user (imaging_api.routers.schemas.CentralHubUser): The user's details on the Central Hub.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        Tuple[imaging_api.routers.schemas.CreatedUser, imaging_api.routers.schemas.User]: The created user and the user
        profile.
    """
    create_user_request = to_create_imaging_user(central_hub_user, headers)
    # Actually create
    user_profile = create_user(create_user_request, headers)
    created_user = CreatedUser(
        username=user_profile.username,
        encrypted_password=encrypt(create_user_request.password),
        email=create_user_request.email,
    )
    return created_user, user_profile


def create_user(user: CreateUser, headers: dict[str, str]) -> User:
    """
    Core logic to create an XNAT user. Returns the newly created user profile.

    Args:
        user (imaging_api.routers.schemas.CreateUser): The user to create.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        imaging_api.routers.schemas.User: The created user profile.
    """
    logger.info(f"Creating user '{user.username}' on XNAT")

    response = requests.post(f"{XNAT_URL}/xapi/users", headers=headers, json=user.model_dump())

    if response.status_code == 201:
        logger.info(f"User '{user.username}' created successfully on XNAT")
        user_profile = get_user_profile_by("username", user.username, headers)
        return user_profile

    elif response.status_code == 409:
        raise AlreadyExistsError(f"Error: User '{user.username}' already exists")
    else:
        raise Exception(f"Error: XNAT user creation failed: {response.status_code} - {response.text}")


def add_user_to_project(user: User, project_id: str, headers: dict[str, str]) -> User:
    """
    Adds a user to a project in XNAT.

    Args:
        user (imaging_api.routers.schemas.User): The user to add.
        project_id (str): The project ID.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        imaging_api.routers.schemas.User: The user profile.

    Raises:
        HTTPException: If there is an error during the addition of the user to the project.
        imaging_api.utils.exceptions.NotFoundError: If the user or project is not found.
    """
    if not user_exists(user.username, headers):
        raise NotFoundError(f"User '{user.username}' not found on XNAT")

    logger.info(f"Adding user '{user.username}' to project '{project_id}'")

    response = requests.put(
        f"{XNAT_URL}/data/projects/{project_id}/users/Members/{user.username}",
        headers=headers,
    )

    if response.status_code == 200:
        logger.info(f"User '{user.username}' added to project '{project_id}'")
        return user
    else:
        raise Exception(
            f"Error: User '{user.username}' could not be added to project: {response.status_code} - {response.text}"
        )
