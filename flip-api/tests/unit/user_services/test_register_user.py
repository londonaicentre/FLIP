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
from uuid import UUID

import pytest
from fastapi import HTTPException, status

from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.interfaces.user import IRegisterUser, IUserResponse
from flip_api.user_services.register_user import register_user


@pytest.fixture
def mock_db():
    """Mock database session fixture."""
    db = MagicMock()
    db.begin.return_value.__enter__ = MagicMock()
    db.begin.return_value.__exit__ = MagicMock()
    return db


@pytest.fixture
def token_id():
    """Token ID fixture."""
    return str(uuid.uuid4())


@pytest.fixture
def user_data():
    """Valid user data fixture."""
    return IRegisterUser(
        email="user1@example.com",
        roles=["5e874994-8528-41a1-82a9-c4b86a41d201", "3c3f280b-ea85-47d9-914f-26774abeb410"],
    )


def test_register_user_success(mock_request, mock_db, token_id, user_data):
    """Test successful user registration."""
    user_pool_id = "eu-west-2_gergtrhrt"
    user_id = UUID("c602d2a4-60e1-70fc-76b5-ac649566cb82")  # Example UUID v7 generated from Cognito

    with (
        patch("flip_api.user_services.register_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.register_user.get_all_roles") as mock_get_all_roles,
        patch("flip_api.user_services.register_user.validate_roles") as mock_validate_roles,
        patch("flip_api.user_services.register_user.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.register_user.create_cognito_user") as mock_create_cognito_user,
    ):
        # Set up mocks
        mock_has_permissions.return_value = True
        mock_get_all_roles.return_value = []
        mock_validate_roles.return_value = None
        mock_get_user_pool_id.return_value = user_pool_id
        mock_create_cognito_user.return_value = user_id

        # Execute
        result = register_user(user_data, mock_request, mock_db, token_id)

        # Assert
        assert isinstance(result, IUserResponse)
        assert result.email == user_data.email
        assert result.roles == user_data.roles
        assert result.user_id == user_id

        # Verify mock calls
        mock_has_permissions.assert_called_once_with(token_id, [PermissionRef.CAN_MANAGE_USERS], mock_db)
        mock_get_user_pool_id.assert_called_once_with(mock_request)
        mock_create_cognito_user.assert_called_once_with(user_data.email, user_pool_id)


def test_permission_denied(mock_request, mock_db, token_id, user_data):
    """Test when user doesn't have the required permissions."""
    with (
        patch("flip_api.user_services.register_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.register_user.logger") as mock_logger,
    ):
        mock_has_permissions.return_value = False

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            register_user(user_data, mock_request, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert f"User with ID: {token_id} was unable to register a user" in exc_info.value.detail
        mock_logger.error.assert_called_once()
        mock_has_permissions.assert_called_once_with(token_id, [PermissionRef.CAN_MANAGE_USERS], mock_db)


def test_user_pool_id_error(mock_request, mock_db, token_id, user_data):
    """Test when getting user pool ID fails."""
    with (
        patch("flip_api.user_services.register_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.register_user.get_all_roles") as mock_get_all_roles,
        patch("flip_api.user_services.register_user.validate_roles") as mock_validate_roles,
        patch("flip_api.user_services.register_user.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.register_user.logger") as mock_logger,
    ):
        # Set up mocks
        mock_has_permissions.return_value = True
        mock_get_all_roles.return_value = []
        mock_validate_roles.return_value = None
        mock_get_user_pool_id.side_effect = Exception("Token does not contain userPoolId")

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            register_user(user_data, mock_request, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Internal server error" in exc_info.value.detail
        mock_logger.error.assert_called_once()


def test_create_cognito_user_error(mock_request, mock_db, token_id, user_data):
    """Test when creating Cognito user fails."""
    user_pool_id = "eu-west-2_gergtrhrt"

    with (
        patch("flip_api.user_services.register_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.register_user.get_all_roles") as mock_get_all_roles,
        patch("flip_api.user_services.register_user.validate_roles") as mock_validate_roles,
        patch("flip_api.user_services.register_user.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.register_user.create_cognito_user") as mock_create_cognito_user,
        patch("flip_api.user_services.register_user.logger") as mock_logger,
    ):
        # Set up mocks
        mock_has_permissions.return_value = True
        mock_get_all_roles.return_value = []
        mock_validate_roles.return_value = None
        mock_get_user_pool_id.return_value = user_pool_id
        mock_create_cognito_user.side_effect = Exception("Failed to create user")

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            register_user(user_data, mock_request, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Internal server error" in exc_info.value.detail
        mock_logger.error.assert_called_once()
