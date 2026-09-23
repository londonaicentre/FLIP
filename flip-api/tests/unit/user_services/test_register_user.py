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

from flip_api.db.models.user_models import PermissionRef, UserProfile, UsersAudit
from flip_api.domain.interfaces.user import IRegisterUser, IUserResponse
from flip_api.user_services.register_user import register_user

NEW_USER_ID = UUID("c602d2a4-60e1-70fc-76b5-ac649566cb82")


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
        name="User One",
        organisation="Example Org",
        roles=["5e874994-8528-41a1-82a9-c4b86a41d201", "3c3f280b-ea85-47d9-914f-26774abeb410"],
    )


def test_register_user_success(fake_idp, mock_request, mock_db, token_id, user_data):
    """The identity provider creates the user; an audit row is written; response carries the new sub."""
    fake_idp.create_user.return_value = NEW_USER_ID

    with (
        patch("flip_api.user_services.register_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.register_user.get_all_roles") as mock_get_all_roles,
        patch("flip_api.user_services.register_user.validate_roles") as mock_validate_roles,
    ):
        mock_has_permissions.return_value = True
        mock_get_all_roles.return_value = []
        mock_validate_roles.return_value = None

        # Execute
        result = register_user(user_data, mock_request, mock_db, token_id, idp=fake_idp)

        # Assert
        assert isinstance(result, IUserResponse)
        assert result.email == user_data.email
        assert result.name == user_data.name
        assert result.organisation == user_data.organisation
        assert result.roles == user_data.roles
        assert result.user_id == NEW_USER_ID

        # Verify mock calls
        mock_has_permissions.assert_called_once_with(token_id, [PermissionRef.CAN_MANAGE_USERS], mock_db)
        fake_idp.create_user.assert_called_once_with(user_data.email)
        fake_idp.delete_user.assert_not_called()

        # Audit row written for the new sub
        assert mock_db.add.call_count == 2
        profile_row = mock_db.add.call_args_list[0][0][0]
        assert isinstance(profile_row, UserProfile)
        assert profile_row.user_id == NEW_USER_ID
        assert profile_row.name == user_data.name
        assert profile_row.organisation == user_data.organisation
        audit_row = mock_db.add.call_args_list[1][0][0]
        assert isinstance(audit_row, UsersAudit)
        assert audit_row.action == "Registered user"
        assert audit_row.user_id == NEW_USER_ID
        assert audit_row.modified_by_user_id == token_id
        mock_db.commit.assert_called_once()


def test_permission_denied(fake_idp, mock_request, mock_db, token_id, user_data):
    """Test when user doesn't have the required permissions."""
    with (
        patch("flip_api.user_services.register_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.register_user.logger") as mock_logger,
    ):
        mock_has_permissions.return_value = False

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            register_user(user_data, mock_request, mock_db, token_id, idp=fake_idp)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert f"User with ID: {token_id} was unable to register a user" in exc_info.value.detail
        mock_logger.error.assert_called_once()
        mock_has_permissions.assert_called_once_with(token_id, [PermissionRef.CAN_MANAGE_USERS], mock_db)
        fake_idp.create_user.assert_not_called()


def test_create_user_error(fake_idp, mock_request, mock_db, token_id, user_data):
    """Test when creating the user in the identity provider fails."""
    fake_idp.create_user.side_effect = Exception("Failed to create user")

    with (
        patch("flip_api.user_services.register_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.register_user.get_all_roles") as mock_get_all_roles,
        patch("flip_api.user_services.register_user.validate_roles") as mock_validate_roles,
        patch("flip_api.user_services.register_user.logger") as mock_logger,
    ):
        # Set up mocks
        mock_has_permissions.return_value = True
        mock_get_all_roles.return_value = []
        mock_validate_roles.return_value = None

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            register_user(user_data, mock_request, mock_db, token_id, idp=fake_idp)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        # Generic detail — no exception text leaked into the response body.
        assert exc_info.value.detail == "Internal server error"
        mock_logger.exception.assert_called_once()
        # Nothing was created, so nothing to roll back and nothing to audit.
        fake_idp.delete_user.assert_not_called()
        mock_db.add.assert_not_called()


def test_audit_commit_failure_rolls_back_provider_user(fake_idp, mock_request, mock_db, token_id, user_data):
    """The identity provider created the user, then the audit-row commit raises.

    Without the rollback, the next retry would hit the provider's "username exists" rejection
    and require manual cleanup. This test pins the rollback contract previously proven by the
    deleted ``test_db_failure_rolls_back_cognito_user`` (now keyed on the audit-row write
    rather than a User row insert).
    """
    fake_idp.create_user.return_value = NEW_USER_ID
    mock_db.commit.side_effect = Exception("DB unavailable")

    with (
        patch("flip_api.user_services.register_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.register_user.get_all_roles") as mock_get_all_roles,
        patch("flip_api.user_services.register_user.validate_roles") as mock_validate_roles,
    ):
        mock_has_permissions.return_value = True
        mock_get_all_roles.return_value = []
        mock_validate_roles.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            register_user(user_data, mock_request, mock_db, token_id, idp=fake_idp)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exc_info.value.detail == "Failed to register user. Please try again."
        # The provider-side user was rolled back so a retry doesn't hit "username exists".
        fake_idp.delete_user.assert_called_once_with(user_data.email)
        mock_db.rollback.assert_called_once()


def test_audit_commit_failure_still_500s_when_provider_rollback_fails(
    fake_idp, mock_request, mock_db, token_id, user_data
):
    """If both the audit commit AND the rollback fail, surface a distinct 500 detail.

    The identity-provider user is orphaned. A retry would hit the provider's "username exists"
    rejection and deterministically fail — telling the caller to "Please try again" is
    misleading. The detail must instead name the orphan state so the operator knows manual
    cleanup is required. ``logger.exception`` records the orphan email.
    """
    fake_idp.create_user.return_value = NEW_USER_ID
    fake_idp.delete_user.side_effect = Exception("Identity provider unreachable too")
    mock_db.commit.side_effect = Exception("DB unavailable")

    with (
        patch("flip_api.user_services.register_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.register_user.get_all_roles") as mock_get_all_roles,
        patch("flip_api.user_services.register_user.validate_roles") as mock_validate_roles,
        patch("flip_api.user_services.register_user.logger") as mock_logger,
    ):
        mock_has_permissions.return_value = True
        mock_get_all_roles.return_value = []
        mock_validate_roles.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            register_user(user_data, mock_request, mock_db, token_id, idp=fake_idp)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        detail = exc_info.value.detail.lower()
        assert "manual cleanup" in detail
        assert "please try again" not in detail
        fake_idp.delete_user.assert_called_once_with(user_data.email)
        # Both the audit-write failure and the rollback failure are logged with stack traces.
        assert mock_logger.exception.call_count >= 2
