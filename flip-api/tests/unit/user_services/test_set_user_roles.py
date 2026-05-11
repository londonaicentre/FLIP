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
from flip_api.user_services.set_user_roles import set_user_roles


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
    return uuid.uuid4()


@pytest.fixture
def token_id():
    """Token ID fixture (caller making the role change)."""
    return uuid.uuid4()


@pytest.fixture
def roles_data(roles_factory):
    """Roles data fixture."""
    return roles_factory()


def test_successful_role_update(mock_db, user_id, token_id, roles_data):
    """Test successful role update.

    Asserts the single-transaction contract: delete-old, insert-new, audit
    are all sent within one commit. A future refactor that splits this back
    into multiple commits would silently risk wiping roles on partial
    failure.
    """
    # Setup: target user exists in Cognito, role_ids_from_db returns the requested roles
    mock_db.exec.return_value.all.return_value = roles_data.roles

    # Mock delete
    mock_db.execute.return_value = MagicMock(rowcount=1)

    with (
        patch("flip_api.user_services.set_user_roles.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.set_user_roles.get_username") as mock_get_username,
        patch("flip_api.user_services.set_user_roles.get_settings") as mock_get_settings,
    ):
        mock_has_permissions.return_value = True
        mock_get_username.return_value = "user@example.com"
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = "pool-id"

        # Execute
        result = set_user_roles(user_id, roles_data, mock_db, token_id)

        # Assert
        assert result == roles_data
        mock_has_permissions.assert_called_once_with(token_id, [PermissionRef.CAN_MANAGE_USERS], mock_db)
        mock_get_username.assert_called_once_with(str(user_id), "pool-id")
        mock_db.exec.assert_called_once()
        # One DELETE for old grants, one add_all for new grants, one add for audit, one commit.
        mock_db.execute.assert_called_once()
        mock_db.add_all.assert_called_once()
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

        # Audit row uses the stable past-tense verb-noun convention; no
        # Python list repr embedded in a free-text column.
        audit_row = mock_db.add.call_args[0][0]
        assert isinstance(audit_row, UsersAudit)
        assert audit_row.action == "Updated user roles"
        assert audit_row.user_id == user_id
        assert audit_row.modified_by_user_id == token_id


def test_cognito_5xx_returns_503(mock_db, user_id, token_id, roles_data):
    """A 5xx from get_username (Cognito read failure) must surface as 503.

    The step-function caller uses 503 to mean "could not verify; do NOT
    destructively roll back the just-created user". A bare 500 here would
    cause a transient Cognito blip to destroy a valid registration.
    """
    with (
        patch("flip_api.user_services.set_user_roles.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.set_user_roles.get_username") as mock_get_username,
        patch("flip_api.user_services.set_user_roles.get_settings") as mock_get_settings,
    ):
        mock_has_permissions.return_value = True
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = "pool-id"
        mock_get_username.side_effect = HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get Cognito users",
        )

        with pytest.raises(HTTPException) as exc_info:
            set_user_roles(user_id, roles_data, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        # No DB writes — we couldn't confirm the user exists, so don't touch grants.
        mock_db.execute.assert_not_called()
        mock_db.add_all.assert_not_called()
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()


def test_cognito_non_404_4xx_propagates_unchanged(mock_db, user_id, token_id, roles_data):
    """Non-404 4xx from get_username (e.g. a future 400 or 429) must propagate.

    A blanket "non-404 -> 503" remap would mask a real 400 (caller bug) or 429
    (rate-limit signal). Only 5xx is genuine "could not verify" and warrants
    503; client-error 4xx codes should stay as themselves so the caller sees
    the actual signal.
    """
    with (
        patch("flip_api.user_services.set_user_roles.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.set_user_roles.get_username") as mock_get_username,
        patch("flip_api.user_services.set_user_roles.get_settings") as mock_get_settings,
    ):
        mock_has_permissions.return_value = True
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = "pool-id"
        mock_get_username.side_effect = HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Cognito throttled",
        )

        with pytest.raises(HTTPException) as exc_info:
            set_user_roles(user_id, roles_data, mock_db, token_id)

        # Original 429 surfaces unchanged — not remapped to 503.
        assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert exc_info.value.detail == "Cognito throttled"
        mock_db.execute.assert_not_called()
        mock_db.add_all.assert_not_called()
        mock_db.commit.assert_not_called()


def test_user_not_found_in_cognito_returns_404(mock_db, user_id, token_id, roles_data):
    """Setting roles for a sub that does not exist in Cognito should return 404, not 500."""
    with (
        patch("flip_api.user_services.set_user_roles.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.set_user_roles.get_username") as mock_get_username,
        patch("flip_api.user_services.set_user_roles.get_settings") as mock_get_settings,
    ):
        mock_has_permissions.return_value = True
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = "pool-id"
        mock_get_username.side_effect = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} is not registered.",
        )

        with pytest.raises(HTTPException) as exc_info:
            set_user_roles(user_id, roles_data, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert str(user_id) in exc_info.value.detail
        mock_db.add_all.assert_not_called()
        mock_db.execute.assert_not_called()


def test_permission_denied(mock_db, user_id, token_id, roles_data):
    """Test when user doesn't have the required permissions."""
    with (
        patch("flip_api.user_services.set_user_roles.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.set_user_roles.logger") as mock_logger,
    ):
        mock_has_permissions.return_value = False

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            set_user_roles(user_id, roles_data, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        mock_logger.error.assert_called_once()


def test_invalid_roles(mock_db, user_id, token_id, roles_data):
    """Test when some roles don't exist in the database."""
    mock_db.exec.return_value.all.return_value = []  # No roles in db

    with (
        patch("flip_api.user_services.set_user_roles.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.set_user_roles.get_username") as mock_get_username,
        patch("flip_api.user_services.set_user_roles.get_settings") as mock_get_settings,
    ):
        mock_has_permissions.return_value = True
        mock_get_username.return_value = "user@example.com"
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = "pool-id"

        # Execute and assert
        with pytest.raises(HTTPException) as exc_info:
            set_user_roles(user_id, roles_data, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid role(s):" in exc_info.value.detail


def test_commit_failure_rolls_back_session(mock_db, user_id, token_id, roles_data):
    """A failed db.commit() must trigger db.rollback() before raising 500.

    Without the rollback the request-scoped session is left in an aborted
    transaction state — close() would discard it, but the explicit
    rollback documents intent and matches the convention used elsewhere
    in user/cohort/private services.
    """
    mock_db.exec.return_value.all.return_value = roles_data.roles
    mock_db.execute.return_value = MagicMock(rowcount=1)
    mock_db.commit.side_effect = Exception("DB unavailable")

    with (
        patch("flip_api.user_services.set_user_roles.has_permissions") as mock_has_permissions,
        patch("flip_api.user_services.set_user_roles.get_username") as mock_get_username,
        patch("flip_api.user_services.set_user_roles.get_settings") as mock_get_settings,
    ):
        mock_has_permissions.return_value = True
        mock_get_username.return_value = "user@example.com"
        mock_get_settings.return_value.AWS_COGNITO_USER_POOL_ID = "pool-id"

        with pytest.raises(HTTPException) as exc_info:
            set_user_roles(user_id, roles_data, mock_db, token_id)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        # SQLAlchemy text must not leak through detail.
        assert "DB unavailable" not in exc_info.value.detail
        mock_db.rollback.assert_called_once()
