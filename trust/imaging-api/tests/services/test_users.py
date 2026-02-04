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

from unittest.mock import patch

import pytest

from imaging_api.routers.schemas import User
from imaging_api.services.users import get_user_profile_by, user_exists
from imaging_api.utils.exceptions import NotFoundError


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
                email="flipiceAccount@aic.co.uk",
                verified=True,
                firstName="flip
                lastName="ServiceAccount",
                lastSuccessfulLogin=None,
            )
        ]
        yield mock


def test_get_user_profile_by_username(mock_get_xnat_users, headers):
    username = "flipiceAccount"
    user = get_user_profile_by("username", username, headers)
    assert user.username == username


def test_get_user_profile_by_email(mock_get_xnat_users, headers):
    email = "flipiceAccount@aic.co.uk"
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
    assert str(e.value) == f"404: User with username '{username}' not found"


def test_get_user_profile_by_nonexistent_email(mock_get_xnat_users, headers):
    mock_get_xnat_users.return_value = []  # Simulate user not found
    email = "nonexistent@user.com"
    with pytest.raises(NotFoundError) as e:
        get_user_profile_by("email", email, headers)
    assert str(e.value) == f"404: User with email '{email}' not found"


def test_user_exists(mock_get_xnat_users, headers):
    username = "flipiceAccount"
    assert user_exists(username, headers)
