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
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.schemas.users import CognitoUser
from flip_api.main import app
from flip_api.user_services.get_user import get_user

client = TestClient(app)


# ---------------------
# Fixtures
# ---------------------


@pytest.fixture(autouse=True)
def override_deps():
    """Override auth dependency for all tests using TestClient."""
    app.dependency_overrides[verify_token] = lambda: uuid4()
    app.dependency_overrides[get_session] = lambda: None
    yield
    app.dependency_overrides.clear()


# ---------------------
# TestClient tests (exercises full response serialization)
# ---------------------


def test_get_user_by_email_response_serialization():
    """Test that the endpoint correctly serializes a CognitoUser response with aliased fields."""
    cognito_user = CognitoUser(id=uuid4(), email="test@example.com", is_disabled=False)

    with (
        patch("flip_api.user_services.get_user.get_user_pool_id", return_value="test-pool"),
        patch("flip_api.user_services.get_user.get_user_by_email_or_id", return_value=cognito_user),
    ):
        response = client.get(f"/api/users/{cognito_user.email}")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == str(cognito_user.id)
    assert data["email"] == cognito_user.email
    assert data["isDisabled"] is False
    assert "is_disabled" not in data


def test_get_user_by_uuid_response_serialization():
    """Test that the endpoint correctly serializes a CognitoUser response when looked up by UUID."""
    user_uuid = uuid4()
    cognito_user = CognitoUser(id=user_uuid, email="test@example.com", is_disabled=True)

    with (
        patch("flip_api.user_services.get_user.get_user_pool_id", return_value="test-pool"),
        patch("flip_api.user_services.get_user.get_user_by_email_or_id", return_value=cognito_user),
    ):
        response = client.get(f"/api/users/{user_uuid}")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == str(user_uuid)
    assert data["isDisabled"] is True


# ---------------------
# Direct function call tests
# ---------------------


def test_get_user_by_email(mock_request, user_email, user_data):
    """Test successfully retrieving a user by email."""
    with (
        patch("flip_api.user_services.get_user.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.get_user.get_user_by_email_or_id") as mock_get_user,
        patch("flip_api.user_services.get_user.GetUserByEmail", side_effect=lambda **kwargs: None),
    ):
        # Set up mocks
        user_pool_id = "test-user-pool"
        mock_get_user_pool_id.return_value = user_pool_id
        mock_get_user.return_value = user_data

        # Execute
        result = get_user(user_email, mock_request)

        # Assert
        assert result == user_data
        mock_get_user_pool_id.assert_called_once_with(mock_request)
        mock_get_user.assert_called_once_with(user_pool_id, email=user_email)


def test_get_user_by_uuid(mock_request, user_id, user_data):
    """Test successfully retrieving a user by UUID."""
    with (
        patch("flip_api.user_services.get_user.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.get_user.get_user_by_email_or_id") as mock_get_user,
        patch("flip_api.user_services.get_user.GetUserById", side_effect=lambda **kwargs: None),
    ):
        # Set up mocks
        user_pool_id = "test-user-pool"
        mock_get_user_pool_id.return_value = user_pool_id
        mock_get_user.return_value = user_data

        # Execute
        result = get_user(user_id, mock_request)

        # Assert
        assert result == user_data
        mock_get_user_pool_id.assert_called_once_with(mock_request)
        mock_get_user.assert_called_once_with(user_pool_id, user_id=UUID(user_id))


def test_invalid_user_id_format(mock_request):
    """Test with an invalid ID format (neither email nor UUID)."""
    invalid_id = "not-an-email-or-uuid"

    # Execute and assert
    with pytest.raises(HTTPException) as exc_info:
        get_user(invalid_id, mock_request)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid user ID format" in exc_info.value.detail


def test_user_not_found(mock_request, user_id):
    """Test when user is not found in Cognito."""
    with (
        patch("flip_api.user_services.get_user.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.get_user.get_user_by_email_or_id") as mock_get_user,
        patch("flip_api.user_services.get_user.GetUserById", side_effect=lambda **kwargs: None),
    ):
        # Set up mocks
        user_pool_id = "test-user-pool"
        mock_get_user_pool_id.return_value = user_pool_id
        mock_get_user.return_value = None  # User not found

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            get_user(user_id, mock_request)

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert f"User '{user_id}' cannot be found" in exc_info.value.detail


def test_internal_server_error(mock_request, user_id):
    """Test handling of internal server errors."""
    with (
        patch("flip_api.user_services.get_user.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.get_user.GetUserById", side_effect=lambda **kwargs: None),
        patch("flip_api.user_services.get_user.logger") as mock_logger,
    ):
        # Set up mocks
        mock_get_user_pool_id.side_effect = Exception("Test exception")

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            get_user(user_id, mock_request)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Internal server error" in exc_info.value.detail
        mock_logger.error.assert_called_once()
