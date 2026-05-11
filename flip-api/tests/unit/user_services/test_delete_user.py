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
    """User ID fixture (a Cognito sub)."""
    return uuid.uuid4()


@pytest.fixture
def token_id():
    """Token ID fixture (caller making the delete)."""
    return uuid.uuid4()


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


def test_cognito_user_already_gone_still_drops_role_grants(mock_request, mock_db, user_id, token_id):
    """If get_username 404s (Cognito side already gone), still reap any ghost role grants and
    write the audit row — but skip the Cognito delete itself.

    This is the idempotency contract: a retry after a partial failure should clean up the DB
    state instead of raising 404 and leaving dangling grants behind.
    """
    user_pool_id = "test-user-pool-id"

    with (
        patch("flip_api.user_services.delete_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.delete_user.get_settings") as mock_get_settings,
        patch("flip_api.user_services.delete_user.get_username") as mock_get_username,
        patch("flip_api.user_services.delete_user.delete_cognito_user") as mock_delete_cognito_user,
    ):
        mock_has_permissions.return_value = True
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = user_pool_id
        mock_get_username.side_effect = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not found"
        )

        result = delete_user(user_id, mock_request, mock_db, token_id)

        assert result == {}
        mock_get_username.assert_called_once_with(str(user_id), user_pool_id)
        # DB cleanup runs even though Cognito is gone — that's the whole point.
        mock_db.execute.assert_called_once()
        mock_db.add.assert_called_once()
        audit_row = mock_db.add.call_args[0][0]
        assert isinstance(audit_row, UsersAudit)
        assert audit_row.action == "Deleted user"
        mock_db.commit.assert_called_once()
        # No Cognito call — there's nothing left to delete on that side.
        mock_delete_cognito_user.assert_not_called()


def test_get_username_non_404_propagates(mock_request, mock_db, user_id, token_id):
    """A 5xx from get_username (e.g. Cognito read failure) must propagate untouched.

    Without this, a transient Cognito read error would silently drop the user's role grants.
    """
    user_pool_id = "test-user-pool-id"

    with (
        patch("flip_api.user_services.delete_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.delete_user.get_settings") as mock_get_settings,
        patch("flip_api.user_services.delete_user.get_username") as mock_get_username,
        patch("flip_api.user_services.delete_user.delete_cognito_user") as mock_delete_cognito_user,
    ):
        mock_has_permissions.return_value = True
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = user_pool_id
        mock_get_username.side_effect = HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="cognito error"
        )

        with pytest.raises(HTTPException) as exc_info:
            delete_user(user_id, mock_request, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        # No DB writes — we couldn't confirm Cognito state, so don't touch the grants.
        mock_db.execute.assert_not_called()
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()
        mock_delete_cognito_user.assert_not_called()


def test_user_deleted_successfully(mock_request, mock_db, user_id, token_id):
    """Happy path: user_role rows dropped, audit row written, Cognito user removed."""
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

        result = delete_user(user_id, mock_request, mock_db, token_id)

        assert result == {}
        mock_get_username.assert_called_once_with(str(user_id), user_pool_id)

        # user_role rows deleted in a single execute() call
        mock_db.execute.assert_called_once()

        # Audit row written
        mock_db.add.assert_called_once()
        audit_row = mock_db.add.call_args[0][0]
        assert isinstance(audit_row, UsersAudit)
        assert audit_row.action == "Deleted user"
        assert audit_row.user_id == user_id
        assert audit_row.modified_by_user_id == token_id

        # DB commits before Cognito delete (so a Cognito failure can't leave dangling grants)
        mock_db.commit.assert_called_once()
        mock_delete_cognito_user.assert_called_once_with(username, user_pool_id)


def test_db_cleanup_durable_when_cognito_delete_fails(mock_request, mock_db, user_id, token_id):
    """Lock the documented ordering: DB cleanup commits BEFORE the Cognito delete.

    If Cognito raises after the DB commit, the role grants are already gone and the audit row
    is durable — preferable to dangling grants on a deleted user. The response detail must
    name the half-deleted state explicitly so the operator knows the user can still
    authenticate but has zero app authority, and that manual Cognito cleanup is required.
    """
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
        # Production path: delete_cognito_user wraps boto3 ClientError as HTTPException.
        mock_delete_cognito_user.side_effect = HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete user"
        )

        with pytest.raises(HTTPException) as exc_info:
            delete_user(user_id, mock_request, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        # Detail must name the half-deleted state so the operator knows what to clean up.
        detail = exc_info.value.detail.lower()
        assert "role grants revoked" in detail
        assert "cognito" in detail
        assert "manual" in detail

        # DB cleanup ran and committed BEFORE the Cognito call.
        mock_db.execute.assert_called_once()
        mock_db.add.assert_called_once()
        audit_row = mock_db.add.call_args[0][0]
        assert isinstance(audit_row, UsersAudit)
        assert audit_row.action == "Deleted user"
        mock_db.commit.assert_called_once()
        mock_delete_cognito_user.assert_called_once_with(username, user_pool_id)
        mock_logger.exception.assert_called_once()
