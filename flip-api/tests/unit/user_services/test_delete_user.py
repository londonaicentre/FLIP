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

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, status

from flip_api.db.models.user_models import PermissionRef
from flip_api.user_services.delete_user import delete_user


@pytest.fixture
def mock_db():
    """Mock database session fixture."""
    db = MagicMock()
    db.begin.return_value.__enter__ = MagicMock()
    db.begin.return_value.__exit__ = MagicMock()
    return db


@pytest.fixture
def user_id():
    """User ID fixture."""
    return str(uuid.uuid4())


@pytest.fixture
def token_id():
    """Token ID fixture."""
    return str(uuid.uuid4())


def test_permission_denied(mock_request, mock_db, user_id, token_id):
    """Test when user doesn't have the required permissions."""
    with (
        patch("flip_api.user_services.delete_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.delete_user.logger") as mock_logger,
    ):
        mock_has_permissions.return_value = False

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            delete_user(user_id, mock_request, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert f"User with ID: {token_id} was unable to manage users" in exc_info.value.detail
        mock_logger.error.assert_called_once()
        mock_has_permissions.assert_called_once_with(token_id, [PermissionRef.CAN_MANAGE_USERS], mock_db)


def test_user_not_found(mock_request, mock_db, user_id, token_id):
    """Test when user doesn't exist in Cognito."""
    user_pool_id = "test-user-pool-id"

    with (
        patch("flip_api.user_services.delete_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.delete_user.get_settings") as mock_get_settings,
        patch("flip_api.user_services.delete_user.get_username") as mock_get_username,
        patch("flip_api.user_services.delete_user.delete_cognito_user") as mock_delete_cognito_user,
    ):
        mock_has_permissions.return_value = True
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = user_pool_id
        mock_get_username.return_value = None  # User not found

        # Execute
        result = delete_user(user_id, mock_request, mock_db, token_id)

        # Assert
        assert result == {}  # Empty response for 204 status
        mock_has_permissions.assert_called_once()
        mock_get_settings.assert_called_once_with()
        mock_get_username.assert_called_once_with(user_id, user_pool_id)
        mock_delete_cognito_user.assert_not_called()  # Should not try to delete non-existent user


def test_user_deleted_successfully(mock_request, mock_db, user_id, token_id):
    """Test successful user deletion."""
    user_pool_id = "test-user-pool-id"
    username = "test@example.com"

    with (
        patch("flip_api.user_services.delete_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.delete_user.get_settings") as mock_get_settings,
        patch("flip_api.user_services.delete_user.get_username") as mock_get_username,
        patch("flip_api.user_services.delete_user.delete_cognito_user") as mock_delete_cognito_user,
    ):
        mock_has_permissions.return_value = True
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = user_pool_id
        mock_get_username.return_value = username
        mock_delete_cognito_user.return_value = True

        # Execute
        result = delete_user(user_id, mock_request, mock_db, token_id)

        # Assert
        assert result == {}  # Empty response for 204 status
        mock_has_permissions.assert_called_once()
        mock_get_settings.assert_called_once_with()
        mock_get_username.assert_called_once_with(user_id, user_pool_id)
        mock_delete_cognito_user.assert_called_once_with(username, user_pool_id)


def test_internal_server_error(mock_request, mock_db, user_id, token_id):
    """Test handling of internal server errors."""
    user_pool_id = "test-user-pool-id"
    username = "test@example.com"

    with (
        patch("flip_api.user_services.delete_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.delete_user.get_settings") as mock_get_settings,
        patch("flip_api.user_services.delete_user.get_username") as mock_get_username,
        patch("flip_api.user_services.delete_user.delete_cognito_user") as mock_delete_cognito_user,
        patch("flip_api.user_services.delete_user.logger") as mock_logger,
    ):
        mock_has_permissions.return_value = True
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = user_pool_id
        mock_get_username.return_value = username
        mock_delete_cognito_user.side_effect = Exception("Test exception")

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            delete_user(user_id, mock_request, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Internal server error" in exc_info.value.detail
        mock_logger.error.assert_called_once()
