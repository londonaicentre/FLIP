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

from flip_api.db.models.user_models import PermissionRef, UsersAudit
from flip_api.user_services.reset_user_mfa import reset_mfa_for_user


@pytest.fixture
def mock_db():
    """Mock database session fixture."""
    return MagicMock()


@pytest.fixture
def user_id():
    return uuid.uuid4()


@pytest.fixture
def token_id():
    return str(uuid.uuid4())


def test_permission_denied(mock_request, mock_db, user_id, token_id):
    """Caller without CAN_MANAGE_USERS gets 403."""
    with (
        patch("flip_api.user_services.reset_user_mfa.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.reset_user_mfa.logger") as mock_logger,
    ):
        mock_has_permissions.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            reset_mfa_for_user(user_id, mock_request, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert f"User with ID: {token_id} was unable to manage users" in exc_info.value.detail
        mock_has_permissions.assert_called_once_with(token_id, [PermissionRef.CAN_MANAGE_USERS], mock_db)
        mock_logger.error.assert_called_once()


def test_user_not_found(mock_request, mock_db, user_id, token_id):
    """Target user missing from Cognito bubbles get_username's 404 and skips the reset call."""
    user_pool_id = "test-user-pool-id"

    with (
        patch("flip_api.user_services.reset_user_mfa.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.reset_user_mfa.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.reset_user_mfa.get_username") as mock_get_username,
        patch("flip_api.user_services.reset_user_mfa.reset_user_mfa") as mock_reset_user_mfa,
    ):
        mock_has_permissions.return_value = True
        mock_get_user_pool_id.return_value = user_pool_id
        mock_get_username.side_effect = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} is not registered.",
        )

        with pytest.raises(HTTPException) as exc_info:
            reset_mfa_for_user(user_id, mock_request, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert f"User with ID {user_id} is not registered." in exc_info.value.detail
        mock_get_username.assert_called_once_with(str(user_id), user_pool_id)
        mock_reset_user_mfa.assert_not_called()


def test_mfa_reset_successfully(mock_request, mock_db, user_id, token_id):
    """Happy path: Cognito MFA is cleared, an audit row is written, endpoint returns empty dict."""
    user_pool_id = "test-user-pool-id"
    username = "user@example.com"

    with (
        patch("flip_api.user_services.reset_user_mfa.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.reset_user_mfa.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.reset_user_mfa.get_username") as mock_get_username,
        patch("flip_api.user_services.reset_user_mfa.reset_user_mfa") as mock_reset_user_mfa,
    ):
        mock_has_permissions.return_value = True
        mock_get_user_pool_id.return_value = user_pool_id
        mock_get_username.return_value = username

        result = reset_mfa_for_user(user_id, mock_request, mock_db, token_id)

        assert result == {}
        mock_reset_user_mfa.assert_called_once_with(username, user_pool_id)

        mock_db.add.assert_called_once()
        audit_row = mock_db.add.call_args[0][0]
        assert isinstance(audit_row, UsersAudit)
        # Stable past-tense verb-noun convention, consistent across services.
        assert audit_row.action == "Reset user MFA"
        assert audit_row.user_id == user_id
        assert audit_row.modified_by_user_id == token_id
        mock_db.commit.assert_called_once()


def test_audit_commit_failure_after_cognito_reset_surfaces_500(mock_request, mock_db, user_id, token_id):
    """Cognito MFA was cleared; the audit-row write then failed.

    The user-visible state already changed, so swallowing the error would
    silently lose the audit. Surface 500 with a generic detail and log the
    Cognito state change at exception level so an operator can reconcile.
    """
    user_pool_id = "test-user-pool-id"
    username = "user@example.com"

    with (
        patch("flip_api.user_services.reset_user_mfa.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.reset_user_mfa.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.reset_user_mfa.get_username") as mock_get_username,
        patch("flip_api.user_services.reset_user_mfa.reset_user_mfa") as mock_reset_user_mfa,
        patch("flip_api.user_services.reset_user_mfa.logger") as mock_logger,
    ):
        mock_has_permissions.return_value = True
        mock_get_user_pool_id.return_value = user_pool_id
        mock_get_username.return_value = username
        mock_db.commit.side_effect = Exception("DB unavailable")

        with pytest.raises(HTTPException) as exc_info:
            reset_mfa_for_user(user_id, mock_request, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        # User-visible detail does NOT echo SQLAlchemy text.
        assert "DB unavailable" not in exc_info.value.detail
        # Cognito reset DID happen; the audit-write failure is logged with rich context.
        mock_reset_user_mfa.assert_called_once_with(username, user_pool_id)
        mock_db.rollback.assert_called_once()
        mock_logger.exception.assert_called()


def test_internal_server_error(mock_request, mock_db, user_id, token_id):
    """Unexpected errors from Cognito bubble up as HTTP 500."""
    user_pool_id = "test-user-pool-id"
    username = "user@example.com"

    with (
        patch("flip_api.user_services.reset_user_mfa.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.reset_user_mfa.get_user_pool_id") as mock_get_user_pool_id,
        patch("flip_api.user_services.reset_user_mfa.get_username") as mock_get_username,
        patch("flip_api.user_services.reset_user_mfa.reset_user_mfa") as mock_reset_user_mfa,
        patch("flip_api.user_services.reset_user_mfa.logger") as mock_logger,
    ):
        mock_has_permissions.return_value = True
        mock_get_user_pool_id.return_value = user_pool_id
        mock_get_username.return_value = username
        mock_reset_user_mfa.side_effect = Exception("boom")

        with pytest.raises(HTTPException) as exc_info:
            reset_mfa_for_user(user_id, mock_request, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exc_info.value.detail == "Failed to reset user MFA"
        # Ensure the raw exception string is NOT leaked to the client.
        assert "boom" not in exc_info.value.detail
        mock_logger.exception.assert_called_once()
