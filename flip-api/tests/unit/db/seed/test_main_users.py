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

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlmodel import Session

from flip_api.db.models.user_models import RoleRef, UserRole
from flip_api.db.seed.main_users import ensure_user_and_role, seed_main_users
from flip_api.domain.schemas.users import CognitoUser
from flip_api.utils.constants import (
    ADMIN_EMAIL_1,
    ADMIN_EMAIL_2,
    ADMIN_EMAIL_3,
    OBSERVER_EMAIL,
    RESEARCHER_EMAIL,
)


@pytest.fixture
def mock_session():
    return MagicMock(spec=Session)


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.AWS_COGNITO_USER_POOL_ID = "test-pool"
    return settings


@patch("flip_api.db.seed.main_users.ensure_user_and_role")
@patch("flip_api.db.seed.main_users.logger")
def test_seed_main_users_calls_ensure_user_and_role(mock_logger, mock_ensure_user_and_role, mock_session):
    """Test that seed_main_users calls ensure_user_and_role for each admin, researcher, and observer."""
    seed_main_users(mock_session)

    assert mock_ensure_user_and_role.call_count == 5

    mock_ensure_user_and_role.assert_any_call(ADMIN_EMAIL_1, RoleRef.ADMIN, mock_session)
    mock_ensure_user_and_role.assert_any_call(ADMIN_EMAIL_2, RoleRef.ADMIN, mock_session)
    mock_ensure_user_and_role.assert_any_call(ADMIN_EMAIL_3, RoleRef.ADMIN, mock_session)
    mock_ensure_user_and_role.assert_any_call(RESEARCHER_EMAIL, RoleRef.RESEARCHER, mock_session)
    mock_ensure_user_and_role.assert_any_call(OBSERVER_EMAIL, RoleRef.OBSERVER, mock_session)

    # Logging verified
    mock_logger.debug.assert_called_with("Seeding main users...")
    mock_logger.info.assert_called_with("✅ Finished seeding main users.")


@patch("flip_api.db.seed.main_users.ensure_user_and_role")
@patch("flip_api.db.seed.main_users.logger")
def test_seed_main_users_continues_after_per_user_http_failure(
    mock_logger, mock_ensure_user_and_role, mock_session
):
    """A transient Cognito read failure on a single user must not tank the whole seed.

    Seeding runs on every API boot and is now Cognito-dependent (Cognito is the source
    of truth). Without this resilience, an HTTP 5xx Cognito blip during deploy would
    couple flip-api liveness to Cognito read-side availability — every subsequent boot
    would fail until both are healthy.
    """
    mock_ensure_user_and_role.side_effect = HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Cognito read transient",
    )

    seed_main_users(mock_session)

    # All five users are still attempted — failure on one does not abort the rest.
    assert mock_ensure_user_and_role.call_count == 5
    # Each failure is logged at warning level so an operator can see what was skipped.
    assert mock_logger.warning.call_count == 5
    # Final completion log still fires.
    mock_logger.info.assert_called_with("✅ Finished seeding main users.")


@patch("flip_api.db.seed.main_users.ensure_user_and_role")
@patch("flip_api.db.seed.main_users.logger")
def test_seed_main_users_propagates_unexpected_errors(
    mock_logger, mock_ensure_user_and_role, mock_session
):
    """A non-HTTP Exception (e.g. programming error, misconfig) still propagates.

    The resilience policy is narrow: tolerate transient Cognito blips, not arbitrary
    bugs. A KeyError or AttributeError on boot is a real defect that should surface
    loudly, not be swallowed.
    """
    mock_ensure_user_and_role.side_effect = RuntimeError("Unexpected programming error")

    with pytest.raises(RuntimeError, match="Unexpected programming error"):
        seed_main_users(mock_session)

    mock_ensure_user_and_role.assert_called_once_with(ADMIN_EMAIL_1, RoleRef.ADMIN, mock_session)


@patch("flip_api.db.seed.main_users.ensure_user_and_role")
@patch("flip_api.db.seed.main_users.logger")
def test_seed_main_users_propagates_4xx_http_failures(
    mock_logger, mock_ensure_user_and_role, mock_session
):
    """A 4xx HTTPException is a definitive caller / config error, not a transient blip.

    Without this, a 400 ("no user email or id provided") from a misconfigured
    constants module would silently boot the platform with missing role grants.
    The resilience wrapper only swallows 5xx (Cognito read transient).
    """
    mock_ensure_user_and_role.side_effect = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="No user email address or ID provided",
    )

    with pytest.raises(HTTPException) as exc_info:
        seed_main_users(mock_session)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    mock_ensure_user_and_role.assert_called_once_with(ADMIN_EMAIL_1, RoleRef.ADMIN, mock_session)


@patch("flip_api.db.seed.main_users.ensure_user_and_role")
@patch("flip_api.db.seed.main_users.logger")
def test_seed_main_users_runs_all_when_each_succeeds(mock_logger, mock_ensure_user_and_role, mock_session):
    """Test that all users are seeded when ensure_user_and_role succeeds."""
    mock_ensure_user_and_role.return_value = None

    seed_main_users(mock_session)

    expected_calls = [
        (ADMIN_EMAIL_1, RoleRef.ADMIN, mock_session),
        (ADMIN_EMAIL_2, RoleRef.ADMIN, mock_session),
        (ADMIN_EMAIL_3, RoleRef.ADMIN, mock_session),
        (RESEARCHER_EMAIL, RoleRef.RESEARCHER, mock_session),
        (OBSERVER_EMAIL, RoleRef.OBSERVER, mock_session),
    ]
    actual_calls = [c.args for c in mock_ensure_user_and_role.call_args_list]
    assert actual_calls == expected_calls

    # Final log
    mock_logger.info.assert_called_with("✅ Finished seeding main users.")


@patch("flip_api.db.seed.main_users.get_settings")
@patch("flip_api.db.seed.main_users.get_user_by_email_or_id")
@patch("flip_api.db.seed.main_users.logger")
def test_ensure_user_and_role_skips_missing_cognito_user(
    mock_logger, mock_get_user_by_email_or_id, mock_get_settings, mock_session, mock_settings
):
    mock_get_settings.return_value = mock_settings
    mock_get_user_by_email_or_id.side_effect = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Not found",
    )

    ensure_user_and_role("missing@example.com", RoleRef.RESEARCHER, mock_session)

    mock_get_user_by_email_or_id.assert_called_once_with(user_pool_id="test-pool", email="missing@example.com")
    mock_session.exec.assert_not_called()
    mock_logger.warning.assert_called_once()


@patch("flip_api.db.seed.main_users.get_settings")
@patch("flip_api.db.seed.main_users.get_user_by_email_or_id")
def test_ensure_user_and_role_reraises_non_404_cognito_errors(
    mock_get_user_by_email_or_id, mock_get_settings, mock_session, mock_settings
):
    mock_get_settings.return_value = mock_settings
    mock_get_user_by_email_or_id.side_effect = HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Cognito failure",
    )

    with pytest.raises(HTTPException) as exc_info:
        ensure_user_and_role("missing@example.com", RoleRef.RESEARCHER, mock_session)

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


@patch("flip_api.db.seed.main_users.get_settings")
@patch("flip_api.db.seed.main_users.get_user_by_email_or_id")
def test_ensure_user_and_role_grants_role_when_missing(
    mock_get_user_by_email_or_id, mock_get_settings, mock_session, mock_settings
):
    """Cognito user exists but has no UserRole row → add the grant and commit."""
    mock_get_settings.return_value = mock_settings
    sub = uuid4()
    mock_get_user_by_email_or_id.return_value = CognitoUser(
        id=sub, email="alex@example.com", is_disabled=False
    )  # type: ignore[call-arg]
    mock_session.exec.return_value.first.return_value = None

    ensure_user_and_role("alex@example.com", RoleRef.RESEARCHER, mock_session)

    mock_session.add.assert_called_once()
    added_obj = mock_session.add.call_args.args[0]
    assert isinstance(added_obj, UserRole)
    assert added_obj.user_id == sub
    assert added_obj.role_id == RoleRef.RESEARCHER.value
    mock_session.commit.assert_called_once()


@patch("flip_api.db.seed.main_users.get_settings")
@patch("flip_api.db.seed.main_users.get_user_by_email_or_id")
def test_ensure_user_and_role_is_idempotent_when_grant_already_exists(
    mock_get_user_by_email_or_id, mock_get_settings, mock_session, mock_settings
):
    """Cognito user exists and already has the role → no add, no commit."""
    mock_get_settings.return_value = mock_settings
    sub = uuid4()
    mock_get_user_by_email_or_id.return_value = CognitoUser(
        id=sub, email="alex@example.com", is_disabled=False
    )  # type: ignore[call-arg]
    mock_session.exec.return_value.first.return_value = UserRole(
        user_id=sub, role_id=RoleRef.RESEARCHER.value
    )

    ensure_user_and_role("alex@example.com", RoleRef.RESEARCHER, mock_session)

    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()
