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
import urllib.parse

import requests

from imaging_api.config import get_settings
from imaging_api.routers.schemas import CentralHubUser, CreatedUser, CreateUser, User
from imaging_api.utils.encryption import encrypt
from imaging_api.utils.exceptions import AlreadyExistsError, NotFoundError
from imaging_api.utils.logger import logger
from imaging_api.utils.passwords import generate_complex_password

XNAT_URL = get_settings().XNAT_URL

# Host-less "set your own password" path a newly created XNAT user follows. It is deliberately
# host-less: XNAT is only reachable from inside the trust enclave, so the hub emails this path and
# tells the recipient to open it against their own XNAT address (FLIP-PT-079). `a`/`s` are the XNAT
# alias-token alias + secret; visiting the link authenticates the user and shows a password form.
XNAT_SETUP_PATH_TEMPLATE = "/app/template/XDATScreen_UpdateUser.vm?a={alias}&s={secret}"


def get_xnat_users(headers: dict[str, str]) -> list[User]:
    """
    Gets all users from XNAT.

    Args:
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[imaging_api.routers.schemas.User]: List of XNAT users.

    Raises:
        Exception: If XNAT returns a non-200 response.
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

    Raises:
        Exception: If fetching existing XNAT users fails.
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

    Raises:
        AssertionError: If ``key`` is not ``"username"`` or ``"email"``.
        imaging_api.utils.exceptions.NotFoundError: If no XNAT user matches ``value`` for the given ``key``.
        Exception: If fetching XNAT users fails.
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


def issue_setup_token(username: str, headers: dict[str, str]) -> str:
    """Mint a single-use XNAT alias token for ``username`` and build the host-less setup path.

    The trust's service account (a site admin) issues the token on the user's behalf via XNAT's
    ``GET /data/services/tokens/issue/user/{username}`` endpoint. Visiting the returned path
    authenticates the user and presents a "set your own password" form; XNAT invalidates the token
    once the password is set. The path is host-less on purpose — XNAT is only reachable from inside
    the trust enclave, so the hub emails the path and tells the recipient to open it against their
    own XNAT address rather than emailing a password (FLIP-PT-079).

    Args:
        username (str): XNAT username to issue the setup token for.
        headers (dict[str, str]): XNAT authentication headers (service-account session).

    Returns:
        str: The host-less setup path, e.g. ``/app/template/XDATScreen_UpdateUser.vm?a=…&s=…``.

    Raises:
        Exception: If XNAT returns a non-200 response when issuing the token.
    """
    quoted_username = urllib.parse.quote(username, safe="")
    response = requests.get(f"{XNAT_URL}/data/services/tokens/issue/user/{quoted_username}", headers=headers)
    if response.status_code != 200:
        raise Exception(f"Error: XNAT setup-token issuance failed: {response.status_code} - {response.text}")

    token = response.json()
    alias = urllib.parse.quote(token["alias"], safe="")
    secret = urllib.parse.quote(token["secret"], safe="")
    return XNAT_SETUP_PATH_TEMPLATE.format(alias=alias, secret=secret)


def create_user_from_central_hub_user(
    central_hub_user: CentralHubUser, headers: dict[str, str]
) -> tuple[CreatedUser, User]:
    """
    Convert central hub user to XNAT CreateUser request object, and create user on XNAT.

    The user is created with a throwaway random password that is never disclosed; instead of
    emailing a password (FLIP-PT-079) we mint a single-use setup token and return the host-less
    link the user follows to set their own password directly in XNAT. The random password is
    load-bearing: XNAT's password-change path raises on a user that has no existing password, so
    the account must be created *with* a password, not passwordless.

    Args:
        central_hub_user (imaging_api.routers.schemas.CentralHubUser): The user's details on the Central Hub.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        tuple[imaging_api.routers.schemas.CreatedUser, imaging_api.routers.schemas.User]: The created user and the user
        profile.
    """
    create_user_request = to_create_imaging_user(central_hub_user, headers)
    # Actually create
    user_profile = create_user(create_user_request, headers)
    setup_path = issue_setup_token(user_profile.username, headers)
    created_user = CreatedUser(
        username=user_profile.username,
        encrypted_setup_path=encrypt(setup_path),
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

    Raises:
        AlreadyExistsError: If a user with the same username already exists on XNAT (HTTP 409).
        Exception: If XNAT returns any other non-201 response.
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
        Exception: If XNAT returns a non-200 status for the project-membership PUT.
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
