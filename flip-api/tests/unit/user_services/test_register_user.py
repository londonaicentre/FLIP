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

from flip_api.db.models.user_models import PermissionRef, UsersAudit
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
    return uuid.uuid4()


@pytest.fixture
def user_data():
    """Valid user data fixture."""
    return IRegisterUser(
        email="user1@example.com",
        roles=["5e874994-8528-41a1-82a9-c4b86a41d201", "3c3f280b-ea85-47d9-914f-26774abeb410"],
    )


def test_register_user_success(mock_request, mock_db, token_id, user_data):
    """Cognito creates the user; an audit row is written; response carries the new sub."""
    user_pool_id = "eu-west-2_gergtrhrt"
    user_id = UUID("c602d2a4-60e1-70fc-76b5-ac649566cb82")

    with (
        patch("flip_api.user_services.register_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.register_user.get_all_roles") as mock_get_all_roles,
        patch("flip_api.user_services.register_user.validate_roles") as mock_validate_roles,
        patch("flip_api.user_services.register_user.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.register_user.create_cognito_user") as mock_create_cognito_user,
    ):
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

        # Audit row written for the new sub
        mock_db.add.assert_called_once()
        audit_row = mock_db.add.call_args[0][0]
        assert isinstance(audit_row, UsersAudit)
        assert audit_row.action == "Registered user"
        assert audit_row.user_id == user_id
        assert audit_row.modified_by_user_id == token_id
        mock_db.commit.assert_called_once()


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
        # Generic detail — no exception text leaked into the response body.
        assert exc_info.value.detail == "Internal server error"
        mock_logger.exception.assert_called_once()


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
        assert exc_info.value.detail == "Internal server error"
        mock_logger.exception.assert_called_once()


def test_audit_commit_failure_rolls_back_cognito_user(mock_request, mock_db, token_id, user_data):
    """Cognito created the user, then the audit-row commit raises.

    Without the rollback, the next retry would hit Cognito's UsernameExistsException and
    require manual cleanup. This test pins the rollback contract previously proven by the
    deleted ``test_db_failure_rolls_back_cognito_user`` (now keyed on the audit-row write
    rather than a User row insert).
    """
    user_pool_id = "eu-west-2_gergtrhrt"
    user_id = UUID("c602d2a4-60e1-70fc-76b5-ac649566cb82")

    with (
        patch("flip_api.user_services.register_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.register_user.get_all_roles") as mock_get_all_roles,
        patch("flip_api.user_services.register_user.validate_roles") as mock_validate_roles,
        patch("flip_api.user_services.register_user.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.register_user.create_cognito_user") as mock_create_cognito_user,
        patch("flip_api.user_services.register_user.delete_cognito_user") as mock_delete_cognito_user,
    ):
        mock_has_permissions.return_value = True
        mock_get_all_roles.return_value = []
        mock_validate_roles.return_value = None
        mock_get_user_pool_id.return_value = user_pool_id
        mock_create_cognito_user.return_value = user_id
        mock_db.commit.side_effect = Exception("DB unavailable")

        with pytest.raises(HTTPException) as exc_info:
            register_user(user_data, mock_request, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exc_info.value.detail == "Failed to register user. Please try again."
        # Cognito was rolled back so a retry doesn't hit UsernameExistsException.
        mock_delete_cognito_user.assert_called_once_with(user_data.email, user_pool_id)
        mock_db.rollback.assert_called_once()


def test_audit_commit_failure_still_500s_when_cognito_rollback_fails(
    mock_request, mock_db, token_id, user_data
):
    """If both the audit commit AND the rollback fail, surface a distinct 500 detail.

    The Cognito user is orphaned. A retry would hit ``UsernameExistsException`` and
    deterministically fail — telling the caller to "Please try again" is misleading.
    The detail must instead name the orphan state so the operator knows manual
    cleanup is required. ``logger.exception`` records the orphan email.
    """
    user_pool_id = "eu-west-2_gergtrhrt"
    user_id = UUID("c602d2a4-60e1-70fc-76b5-ac649566cb82")

    with (
        patch("flip_api.user_services.register_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.register_user.get_all_roles") as mock_get_all_roles,
        patch("flip_api.user_services.register_user.validate_roles") as mock_validate_roles,
        patch("flip_api.user_services.register_user.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.register_user.create_cognito_user") as mock_create_cognito_user,
        patch("flip_api.user_services.register_user.delete_cognito_user") as mock_delete_cognito_user,
        patch("flip_api.user_services.register_user.logger") as mock_logger,
    ):
        mock_has_permissions.return_value = True
        mock_get_all_roles.return_value = []
        mock_validate_roles.return_value = None
        mock_get_user_pool_id.return_value = user_pool_id
        mock_create_cognito_user.return_value = user_id
        mock_db.commit.side_effect = Exception("DB unavailable")
        mock_delete_cognito_user.side_effect = Exception("Cognito unreachable too")

        with pytest.raises(HTTPException) as exc_info:
            register_user(user_data, mock_request, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        detail = exc_info.value.detail.lower()
        assert "manual cleanup" in detail
        assert "please try again" not in detail
        mock_delete_cognito_user.assert_called_once_with(user_data.email, user_pool_id)
        # Both the audit-write failure and the rollback failure are logged with stack traces.
        assert mock_logger.exception.call_count >= 2
