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

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from imaging_api.routers.schemas import CentralHubUser, CreateUser, User
from imaging_api.services.users import (
    add_user_to_project,
    create_user,
    create_user_from_central_hub_user,
    get_user_profile_by,
    get_xnat_users,
    to_create_imaging_user,
    user_exists,
)
from imaging_api.utils.exceptions import AlreadyExistsError, NotFoundError


@pytest.fixture
def headers():
    return {}


# Create a fixture to patch get_xnat_users
@pytest.fixture
def mock_get_xnat_users():
    with patch("imaging_api.services.users.get_xnat_users") as mock:
        mock.return_value = [
            User(
                lastModified=1234567890,
                username="flipServiceAccount",
                enabled=True,
                id=1,
                secured=False,
                email="flipServiceAccount@aic.co.uk",
                verified=True,
                firstName="flip",
                lastName="ServiceAccount",
                lastSuccessfulLogin=None,
            )
        ]
        yield mock


def test_get_user_profile_by_username(mock_get_xnat_users, headers):
    username = "flipServiceAccount"
    user = get_user_profile_by("username", username, headers)
    assert user.username == username


def test_get_user_profile_by_email(mock_get_xnat_users, headers):
    email = "flipServiceAccount@aic.co.uk"
    user = get_user_profile_by("email", email, headers)
    assert user.email == email


def test_get_user_profile_by_invalid_key(mock_get_xnat_users, headers):
    invalid_key = "invalid"
    with pytest.raises(AssertionError) as e:
        get_user_profile_by(invalid_key, "value", headers)
    assert str(e.value) == f"Invalid key: {invalid_key}, must be 'username' or 'email'"


def test_get_user_profile_by_nonexistent_username(mock_get_xnat_users, headers):
    mock_get_xnat_users.return_value = []  # Simulate user not found
    username = "nonexistent"
    with pytest.raises(NotFoundError) as e:
        get_user_profile_by("username", username, headers)
    assert str(e.value) == "404: User not found by username"


def test_get_user_profile_by_nonexistent_email(mock_get_xnat_users, headers):
    mock_get_xnat_users.return_value = []  # Simulate user not found
    email = "nonexistent@user.com"
    with pytest.raises(NotFoundError) as e:
        get_user_profile_by("email", email, headers)
    assert str(e.value) == "404: User not found by email"


def test_user_exists(mock_get_xnat_users, headers):
    username = "flipServiceAccount"
    assert user_exists(username, headers)


def test_user_exists_false(mock_get_xnat_users, headers):
    assert not user_exists("nonexistent", headers)


# ---------------------------------------------------------------------------
# get_xnat_users
# ---------------------------------------------------------------------------
@patch("imaging_api.services.users.requests.get")
def test_get_xnat_users_success(mock_get, headers):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value=[
            {
                "lastModified": 123, "username": "alice", "enabled": True,
                "id": 1, "secured": False, "email": "alice@test.com",
                "verified": True, "firstName": "Alice", "lastName": "A",
            },
        ]),
    )
    users = get_xnat_users(headers)
    assert len(users) == 1
    assert users[0].username == "alice"


@patch("imaging_api.services.users.requests.get")
def test_get_xnat_users_failure(mock_get, headers):
    mock_get.return_value = MagicMock(status_code=500, text="Server Error")
    # The function creates User objects before checking status code, but still
    # raises on non-200
    with pytest.raises(Exception, match="Getting XNAT users failed"):
        get_xnat_users(headers)


# ---------------------------------------------------------------------------
# get_user_profile_by — error fetching users
# ---------------------------------------------------------------------------
@patch("imaging_api.services.users.get_xnat_users")
def test_get_user_profile_by_fetch_error(mock_get_users, headers):
    mock_get_users.side_effect = Exception("connection timeout")

    with pytest.raises(Exception, match="XNAT error when fetching users"):
        get_user_profile_by("username", "alice", headers)


# ---------------------------------------------------------------------------
# to_create_imaging_user
# ---------------------------------------------------------------------------
@patch("imaging_api.services.users.generate_complex_password", return_value="P@ssw0rd123456!")
@patch("imaging_api.services.users.get_xnat_users")
def test_to_create_imaging_user_no_conflict(mock_get_users, mock_pwd, headers):
    mock_get_users.return_value = []
    hub_user = CentralHubUser(id=uuid4(), email="john.doe@hospital.nhs.uk")

    create_user_req = to_create_imaging_user(hub_user, headers)
    assert create_user_req.username == "johndoe"
    assert create_user_req.email == "john.doe@hospital.nhs.uk"
    assert create_user_req.enabled is True


@patch("imaging_api.services.users.generate_complex_password", return_value="P@ssw0rd123456!")
@patch("imaging_api.services.users.get_xnat_users")
def test_to_create_imaging_user_with_conflict(mock_get_users, mock_pwd, headers):
    existing_user = MagicMock()
    existing_user.username = "johndoe"
    mock_get_users.return_value = [existing_user]
    hub_user = CentralHubUser(id=uuid4(), email="john.doe@hospital.nhs.uk")

    create_user_req = to_create_imaging_user(hub_user, headers)
    # Should append suffix to avoid collision
    assert create_user_req.username == "john.doe1"


@patch("imaging_api.services.users.generate_complex_password", return_value="P@ssw0rd123456!")
@patch("imaging_api.services.users.get_xnat_users")
def test_to_create_imaging_user_disabled(mock_get_users, mock_pwd, headers):
    mock_get_users.return_value = []
    hub_user = CentralHubUser(id=uuid4(), email="disabled@test.com", is_disabled=True)

    create_user_req = to_create_imaging_user(hub_user, headers)
    assert create_user_req.enabled is False


@patch("imaging_api.services.users.get_xnat_users")
def test_to_create_imaging_user_fetch_error(mock_get_users, headers):
    mock_get_users.side_effect = Exception("XNAT down")
    hub_user = CentralHubUser(id=uuid4(), email="user@test.com")

    with pytest.raises(Exception, match="XNAT error when fetching users"):
        to_create_imaging_user(hub_user, headers)


# ---------------------------------------------------------------------------
# create_user
# ---------------------------------------------------------------------------
_SAMPLE_USER_DICT = {
    "lastModified": 123, "username": "alice", "enabled": True,
    "id": 1, "secured": False, "email": "alice@test.com",
    "verified": True, "firstName": "Alice", "lastName": "A",
}


@patch("imaging_api.services.users.get_user_profile_by")
@patch("imaging_api.services.users.requests.post")
def test_create_user_success(mock_post, mock_get_profile, headers):
    mock_post.return_value = MagicMock(status_code=201)
    mock_get_profile.return_value = User(**_SAMPLE_USER_DICT)

    user_req = CreateUser(
        username="alice", password="pass", firstName="Alice",
        lastName="A", email="alice@test.com",
    )
    profile = create_user(user_req, headers)
    assert profile.username == "alice"


@patch("imaging_api.services.users.requests.post")
def test_create_user_conflict(mock_post, headers):
    mock_post.return_value = MagicMock(status_code=409, text="conflict")

    user_req = CreateUser(
        username="alice", password="pass", firstName="Alice",
        lastName="A", email="alice@test.com",
    )
    with pytest.raises(AlreadyExistsError, match="already exists"):
        create_user(user_req, headers)


@patch("imaging_api.services.users.requests.post")
def test_create_user_server_error(mock_post, headers):
    mock_post.return_value = MagicMock(status_code=500, text="Server Error")

    user_req = CreateUser(
        username="alice", password="pass", firstName="Alice",
        lastName="A", email="alice@test.com",
    )
    with pytest.raises(Exception, match="XNAT user creation failed"):
        create_user(user_req, headers)


# ---------------------------------------------------------------------------
# create_user_from_central_hub_user
# ---------------------------------------------------------------------------
@patch("imaging_api.services.users.encrypt", return_value="encrypted_pwd")
@patch("imaging_api.services.users.create_user")
@patch("imaging_api.services.users.to_create_imaging_user")
def test_create_user_from_central_hub_user(mock_to_create, mock_create, mock_encrypt, headers):
    mock_to_create.return_value = CreateUser(
        username="alice", password="secret", firstName="Alice",
        lastName="A", email="alice@test.com",
    )
    mock_create.return_value = User(**_SAMPLE_USER_DICT)

    hub_user = CentralHubUser(id=uuid4(), email="alice@test.com")
    created_user, user_profile = create_user_from_central_hub_user(hub_user, headers)

    assert created_user.username == "alice"
    assert created_user.encrypted_password == "encrypted_pwd"
    assert user_profile.username == "alice"


# ---------------------------------------------------------------------------
# add_user_to_project
# ---------------------------------------------------------------------------
@patch("imaging_api.services.users.requests.put")
@patch("imaging_api.services.users.user_exists", return_value=True)
def test_add_user_to_project_success(mock_exists, mock_put, headers):
    mock_put.return_value = MagicMock(status_code=200)
    user = User(**_SAMPLE_USER_DICT)

    result = add_user_to_project(user, "PROJ1", headers)
    assert result.username == "alice"


@patch("imaging_api.services.users.user_exists", return_value=False)
def test_add_user_to_project_not_found(mock_exists, headers):
    user = User(**_SAMPLE_USER_DICT)

    with pytest.raises(NotFoundError, match="not found on XNAT"):
        add_user_to_project(user, "PROJ1", headers)


@patch("imaging_api.services.users.requests.put")
@patch("imaging_api.services.users.user_exists", return_value=True)
def test_add_user_to_project_failure(mock_exists, mock_put, headers):
    mock_put.return_value = MagicMock(status_code=403, text="Forbidden")
    user = User(**_SAMPLE_USER_DICT)

    with pytest.raises(Exception, match="could not be added to project"):
        add_user_to_project(user, "PROJ1", headers)
