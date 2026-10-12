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

USERNAME = "test@example.com"


@pytest.fixture
def mock_db():
    """Mock database session fixture."""
    db = MagicMock()
    db.begin.return_value.__enter__ = MagicMock()
    db.begin.return_value.__exit__ = MagicMock()
    return db


@pytest.fixture
def user_id():
    """User ID fixture (an identity-provider sub)."""
    return uuid.uuid4()


@pytest.fixture
def token_id():
    """Token ID fixture (caller making the delete)."""
    return uuid.uuid4()


def test_permission_denied(fake_idp, mock_request, mock_db, user_id, token_id):
    """Test when user doesn't have the required permissions."""
    with (
        patch("flip_api.user_services.delete_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.delete_user.logger") as mock_logger,
    ):
        mock_has_permissions.return_value = False

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            delete_user(user_id, mock_request, mock_db, token_id, idp=fake_idp)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert f"User with ID: {token_id} was unable to manage users" in exc_info.value.detail
        mock_logger.error.assert_called_once()
        mock_has_permissions.assert_called_once_with(token_id, [PermissionRef.CAN_MANAGE_USERS], mock_db)
        fake_idp.get_username.assert_not_called()
        fake_idp.delete_user.assert_not_called()


def test_provider_user_already_gone_still_drops_role_grants(fake_idp, mock_request, mock_db, user_id, token_id):
    """If get_username 404s (provider side already gone), still reap any ghost role grants and
    write the audit row — but skip the provider-side delete itself.

    This is the idempotency contract: a retry after a partial failure should clean up the DB
    state instead of raising 404 and leaving dangling grants behind.
    """
    fake_idp.get_username.side_effect = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    with patch("flip_api.user_services.delete_user.has_permissions") as mock_has_permissions:
        mock_has_permissions.return_value = True

        result = delete_user(user_id, mock_request, mock_db, token_id, idp=fake_idp)

        assert result == {}
        fake_idp.get_username.assert_called_once_with(user_id)
        # DB cleanup runs even though the provider-side user is gone — that's the whole point.
        mock_db.execute.assert_called_once()
        mock_db.add.assert_called_once()
        audit_row = mock_db.add.call_args[0][0]
        assert isinstance(audit_row, UsersAudit)
        assert audit_row.action == "Deleted user"
        mock_db.commit.assert_called_once()
        # No provider call — there's nothing left to delete on that side.
        fake_idp.delete_user.assert_not_called()


def test_get_username_non_404_propagates(fake_idp, mock_request, mock_db, user_id, token_id):
    """A 5xx from get_username (e.g. an identity-provider read failure) must propagate untouched.

    Without this, a transient provider read error would silently drop the user's role grants.
    """
    fake_idp.get_username.side_effect = HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="identity provider error"
    )

    with patch("flip_api.user_services.delete_user.has_permissions") as mock_has_permissions:
        mock_has_permissions.return_value = True

        with pytest.raises(HTTPException) as exc_info:
            delete_user(user_id, mock_request, mock_db, token_id, idp=fake_idp)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exc_info.value.detail == "identity provider error"
        # No DB writes — we couldn't confirm the provider-side state, so don't touch the grants.
        mock_db.execute.assert_not_called()
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()
        fake_idp.delete_user.assert_not_called()


def test_user_deleted_successfully(fake_idp, mock_request, mock_db, user_id, token_id):
    """Happy path: user_role rows dropped, audit row written, provider-side user removed."""
    fake_idp.get_username.return_value = USERNAME

    with patch("flip_api.user_services.delete_user.has_permissions") as mock_has_permissions:
        mock_has_permissions.return_value = True

        result = delete_user(user_id, mock_request, mock_db, token_id, idp=fake_idp)

        assert result == {}
        fake_idp.get_username.assert_called_once_with(user_id)

        # user_role rows deleted in a single execute() call
        mock_db.execute.assert_called_once()

        # Audit row written
        mock_db.add.assert_called_once()
        audit_row = mock_db.add.call_args[0][0]
        assert isinstance(audit_row, UsersAudit)
        assert audit_row.action == "Deleted user"
        assert audit_row.user_id == user_id
        assert audit_row.modified_by_user_id == token_id

        # DB commits before the provider-side delete (so a provider failure can't leave dangling grants)
        mock_db.commit.assert_called_once()
        fake_idp.delete_user.assert_called_once_with(USERNAME)


def test_db_cleanup_durable_when_provider_delete_fails(fake_idp, mock_request, mock_db, user_id, token_id):
    """Lock the documented ordering: DB cleanup commits BEFORE the provider-side delete.

    If the identity provider raises after the DB commit, the role grants are already gone and the
    audit row is durable — preferable to dangling grants on a deleted user. The response detail
    must name the half-deleted state explicitly so the operator knows the user can still
    authenticate but has zero app authority, and that manual provider-side cleanup is required.
    """
    fake_idp.get_username.return_value = USERNAME
    # Production path: the provider wraps its client error as HTTPException.
    fake_idp.delete_user.side_effect = HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete user"
    )

    with (
        patch("flip_api.user_services.delete_user.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.delete_user.logger") as mock_logger,
    ):
        mock_has_permissions.return_value = True

        with pytest.raises(HTTPException) as exc_info:
            delete_user(user_id, mock_request, mock_db, token_id, idp=fake_idp)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        # Detail must name the half-deleted state so the operator knows what to clean up.
        detail = exc_info.value.detail.lower()
        assert "role grants revoked" in detail
        assert "identity-provider" in detail
        assert "manual" in detail

        # DB cleanup ran and committed BEFORE the provider call.
        mock_db.execute.assert_called_once()
        mock_db.add.assert_called_once()
        audit_row = mock_db.add.call_args[0][0]
        assert isinstance(audit_row, UsersAudit)
        assert audit_row.action == "Deleted user"
        mock_db.commit.assert_called_once()
        fake_idp.delete_user.assert_called_once_with(USERNAME)
        mock_logger.exception.assert_called_once()
