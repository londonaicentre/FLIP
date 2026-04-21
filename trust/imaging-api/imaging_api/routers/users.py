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

from typing import Annotated

import requests
from fastapi import APIRouter, Depends, HTTPException

from imaging_api.config import get_settings
from imaging_api.routers.schemas import CreateUser, UpdateUser, User
from imaging_api.services.users import (
    add_user_to_project,
    create_user,
    get_user_profile_by,
    get_xnat_users,
)
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.exceptions import NotFoundError
from imaging_api.utils.logger import logger

XNAT_URL = get_settings().XNAT_URL

router = APIRouter(prefix="/users", tags=["Users"])

XNATAuthHeaders = Annotated[dict[str, str], Depends(get_xnat_auth_headers)]


@router.get("", summary="Get XNAT Users")
def get_users(headers: XNATAuthHeaders) -> list[User]:
    """Get a list of all users on XNAT.

    Args:
        headers (XNATAuthHeaders): XNAT authentication headers injected via FastAPI dependency.

    Returns:
        list[User]: All users currently registered on the XNAT instance.
    """
    return get_xnat_users(headers)


@router.post("", summary="Create XNAT User")
def create_user_endpoint(user: CreateUser, headers: XNATAuthHeaders) -> User:
    """
    Creates a new user on XNAT.

    Args:
        user (imaging_api.routers.schemas.CreateUser): User data to create.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        imaging_api.routers.schemas.User: The created user profile.

    Raises:
        HTTPException: If there is an error during the creation of the user.
    """
    try:
        return create_user(user, headers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("", summary="Update XNAT User")
def update_user_profile(update_profile_request: UpdateUser, headers: XNATAuthHeaders) -> User:
    """
    Updates the profile of a user on XNAT.

    Args:
        update_profile_request (imaging_api.routers.schemas.UpdateUser): User data to update.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        imaging_api.routers.schemas.User: The updated user profile.

    Raises:
        HTTPException: If there is an error during the update of the user profile or if the request cannot be processed.
    """
    # Get user
    try:
        user = get_user_profile_by("email", update_profile_request.email, headers)
        username = user.username
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    response = requests.put(
        f"{XNAT_URL}/xapi/users/{username}",
        headers=headers,
        json=update_profile_request.model_dump(mode="json"),
    )

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")

    if response.status_code == 304:
        logger.warning(f"User '{username}' not modified")
        return user

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    # If the user was updated successfully, return the updated user profile
    user_data = response.json()
    return User(**user_data)


@router.put(
    "/add-to-project/{username}/{project_id}",
    summary="Add User to Project",
    description="Add a user to an imaging project.",
)
def add_user_to_project_endpoint(username: str, project_id: str, headers: XNATAuthHeaders) -> User:
    """
    Adds an XNAT user to a project.

    In XNAT, username is unique, so it can be used to identify the user. If you try to add a user with an existing
    username, XNAT will return an error. The error message will look like this:

        "ERROR: The username {username} is already in use. Please enter a different username value and try again."

    Added checks to ensure the user exists and is enabled before adding them to the project. This is not something that
    is done in the XNAT function.

    Args:
        username (str): The username of the user to add.
        project_id (str): The ID of the project to add the user to.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        imaging_api.routers.schemas.User: The updated user profile.

    Raises:
        HTTPException: If there is an error getting the user profile or if there is an error adding the user to the
        project.
    """
    try:
        user = get_user_profile_by("username", username, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        return add_user_to_project(user, project_id, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
