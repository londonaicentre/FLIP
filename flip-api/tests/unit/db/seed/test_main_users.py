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
from sqlmodel import Session

from flip_api.auth.identity import (
    IdentityProvider,
    IdentityProviderError,
    InvalidIdentifierError,
    UserNotFoundError,
)
from flip_api.db.models.user_models import RoleRef, UserProfile, UserRole
from flip_api.db.seed.main_users import MAIN_USER_PROFILES, ensure_user_and_role, seed_main_users
from flip_api.domain.schemas.users import CognitoUser
from flip_api.utils.constants import (
    ADMIN_EMAIL_1,
    ADMIN_EMAIL_2,
    ADMIN_EMAIL_3,
    DEMO_ADMIN_EMAIL,
    DEMO_RESEARCHER_EMAIL,
    RESEARCHER_EMAIL,
    VIEWER_EMAIL,
)


@pytest.fixture
def mock_session():
    return MagicMock(spec=Session)


@pytest.fixture
def idp():
    """The raw (non-HTTP) provider the seed drives: it speaks the neutral exceptions, not HTTPException."""
    return MagicMock(spec=IdentityProvider)


@pytest.fixture
def mock_build_identity_provider(idp):
    """``seed_main_users`` builds one provider for the whole run; hand it our double."""
    with patch("flip_api.db.seed.main_users.build_identity_provider", return_value=idp) as mock_build:
        yield mock_build


@patch("flip_api.db.seed.main_users.ensure_user_and_role")
@patch("flip_api.db.seed.main_users.logger")
def test_seed_main_users_calls_ensure_user_and_role(
    mock_logger, mock_ensure_user_and_role, mock_session, idp, mock_build_identity_provider
):
    """Test that seed_main_users calls ensure_user_and_role for each admin, researcher, and viewer."""
    seed_main_users(mock_session)

    # One provider for the whole seed, passed first to every call.
    mock_build_identity_provider.assert_called_once_with()
    assert mock_ensure_user_and_role.call_count == 7

    mock_ensure_user_and_role.assert_any_call(
        idp, ADMIN_EMAIL_1, RoleRef.ADMIN, mock_session, *MAIN_USER_PROFILES[ADMIN_EMAIL_1]
    )
    mock_ensure_user_and_role.assert_any_call(
        idp, ADMIN_EMAIL_2, RoleRef.ADMIN, mock_session, *MAIN_USER_PROFILES[ADMIN_EMAIL_2]
    )
    mock_ensure_user_and_role.assert_any_call(
        idp, ADMIN_EMAIL_3, RoleRef.ADMIN, mock_session, *MAIN_USER_PROFILES[ADMIN_EMAIL_3]
    )
    mock_ensure_user_and_role.assert_any_call(
        idp, RESEARCHER_EMAIL, RoleRef.RESEARCHER, mock_session, *MAIN_USER_PROFILES[RESEARCHER_EMAIL]
    )
    mock_ensure_user_and_role.assert_any_call(
        idp, VIEWER_EMAIL, RoleRef.VIEWER, mock_session, *MAIN_USER_PROFILES[VIEWER_EMAIL]
    )

    # Logging verified
    mock_logger.debug.assert_called_with("Seeding main users...")
    mock_logger.info.assert_called_with("✅ Finished seeding main users.")


@patch("flip_api.db.seed.main_users.ensure_user_and_role")
@patch("flip_api.db.seed.main_users.logger")
def test_seed_main_users_continues_after_per_user_identity_provider_error(
    mock_logger, mock_ensure_user_and_role, mock_session, idp, mock_build_identity_provider
):
    """A transient identity-provider read failure on a single user must not tank the whole seed.

    Seeding runs on every API boot and is provider-dependent (the provider is the source
    of truth). Without this resilience, a provider blip during deploy would couple flip-api
    liveness to the provider's read-side availability — every subsequent boot would fail
    until both are healthy.
    """
    mock_ensure_user_and_role.side_effect = IdentityProviderError("identity-provider read transient")

    seed_main_users(mock_session)

    # All seven users are still attempted — failure on one does not abort the rest.
    assert mock_ensure_user_and_role.call_count == 7
    # Each failure is logged at warning level so an operator can see what was skipped.
    assert mock_logger.warning.call_count == 7
    # Final completion log still fires.
    mock_logger.info.assert_called_with("✅ Finished seeding main users.")


@patch("flip_api.db.seed.main_users.ensure_user_and_role")
@patch("flip_api.db.seed.main_users.logger")
def test_seed_main_users_propagates_unexpected_errors(
    mock_logger, mock_ensure_user_and_role, mock_session, idp, mock_build_identity_provider
):
    """A non-provider Exception (e.g. programming error, misconfig) still propagates.

    The resilience policy is narrow: tolerate transient provider blips, not arbitrary
    bugs. A KeyError or AttributeError on boot is a real defect that should surface
    loudly, not be swallowed.
    """
    mock_ensure_user_and_role.side_effect = RuntimeError("Unexpected programming error")

    with pytest.raises(RuntimeError, match="Unexpected programming error"):
        seed_main_users(mock_session)

    mock_ensure_user_and_role.assert_called_once_with(
        idp, ADMIN_EMAIL_1, RoleRef.ADMIN, mock_session, *MAIN_USER_PROFILES[ADMIN_EMAIL_1]
    )


@patch("flip_api.db.seed.main_users.ensure_user_and_role")
@patch("flip_api.db.seed.main_users.logger")
def test_seed_main_users_propagates_invalid_identifier_errors(
    mock_logger, mock_ensure_user_and_role, mock_session, idp, mock_build_identity_provider
):
    """``InvalidIdentifierError`` is a definitive caller / config error, not a transient blip.

    Without this, a malformed well-known email in the constants module would silently
    boot the platform with missing role grants. The resilience wrapper only swallows the
    other ``IdentityProviderError`` kinds (read transients).
    """
    mock_ensure_user_and_role.side_effect = InvalidIdentifierError("No user email address or ID provided")

    with pytest.raises(InvalidIdentifierError, match="No user email address or ID provided"):
        seed_main_users(mock_session)

    mock_ensure_user_and_role.assert_called_once_with(
        idp, ADMIN_EMAIL_1, RoleRef.ADMIN, mock_session, *MAIN_USER_PROFILES[ADMIN_EMAIL_1]
    )
    mock_logger.warning.assert_not_called()


@patch("flip_api.db.seed.main_users.ensure_user_and_role")
@patch("flip_api.db.seed.main_users.logger")
def test_seed_main_users_runs_all_when_each_succeeds(
    mock_logger, mock_ensure_user_and_role, mock_session, idp, mock_build_identity_provider
):
    """Test that all users are seeded when ensure_user_and_role succeeds."""
    mock_ensure_user_and_role.return_value = None

    seed_main_users(mock_session)

    expected_calls = [
        (idp, ADMIN_EMAIL_1, RoleRef.ADMIN, mock_session, *MAIN_USER_PROFILES[ADMIN_EMAIL_1]),
        (idp, ADMIN_EMAIL_2, RoleRef.ADMIN, mock_session, *MAIN_USER_PROFILES[ADMIN_EMAIL_2]),
        (idp, ADMIN_EMAIL_3, RoleRef.ADMIN, mock_session, *MAIN_USER_PROFILES[ADMIN_EMAIL_3]),
        (idp, RESEARCHER_EMAIL, RoleRef.RESEARCHER, mock_session, *MAIN_USER_PROFILES[RESEARCHER_EMAIL]),
        (idp, VIEWER_EMAIL, RoleRef.VIEWER, mock_session, *MAIN_USER_PROFILES[VIEWER_EMAIL]),
        (idp, DEMO_RESEARCHER_EMAIL, RoleRef.RESEARCHER, mock_session, *MAIN_USER_PROFILES[DEMO_RESEARCHER_EMAIL]),
        (idp, DEMO_ADMIN_EMAIL, RoleRef.ADMIN, mock_session, *MAIN_USER_PROFILES[DEMO_ADMIN_EMAIL]),
    ]
    actual_calls = [c.args for c in mock_ensure_user_and_role.call_args_list]
    assert actual_calls == expected_calls

    # Final log
    mock_logger.info.assert_called_with("✅ Finished seeding main users.")


@patch("flip_api.db.seed.main_users.logger")
def test_ensure_user_and_role_skips_missing_user(mock_logger, mock_session, idp):
    """A well-known email with no provider user is skipped with a warning, not an error."""
    idp.get_user.side_effect = UserNotFoundError("Not found")

    ensure_user_and_role(idp, "missing@example.com", RoleRef.RESEARCHER, mock_session, "Missing User", "Example Org")

    idp.get_user.assert_called_once_with(email="missing@example.com")
    mock_session.exec.assert_not_called()
    mock_session.add.assert_not_called()
    mock_logger.warning.assert_called_once()


def test_ensure_user_and_role_reraises_other_provider_errors(mock_session, idp):
    """Only ``UserNotFoundError`` is a skip; any other provider failure is the caller's to handle."""
    idp.get_user.side_effect = IdentityProviderError("identity-provider failure")

    with pytest.raises(IdentityProviderError, match="identity-provider failure"):
        ensure_user_and_role(
            idp, "missing@example.com", RoleRef.RESEARCHER, mock_session, "Missing User", "Example Org"
        )

    mock_session.exec.assert_not_called()


def test_ensure_user_and_role_grants_role_when_missing(mock_session, idp):
    """Provider user exists but has no UserRole row → add the grant and commit."""
    sub = uuid4()
    idp.get_user.return_value = CognitoUser(id=sub, email="alex@example.com", is_disabled=False)  # type: ignore[call-arg]
    mock_session.get.return_value = None
    mock_session.exec.return_value.first.return_value = None

    ensure_user_and_role(idp, "alex@example.com", RoleRef.RESEARCHER, mock_session, "Alex Example", "Example Org")

    idp.get_user.assert_called_once_with(email="alex@example.com")
    assert mock_session.add.call_count == 2
    added_profile = mock_session.add.call_args_list[0].args[0]
    assert isinstance(added_profile, UserProfile)
    assert added_profile.user_id == sub
    assert added_profile.name == "Alex Example"
    assert added_profile.organisation == "Example Org"
    added_role = mock_session.add.call_args_list[1].args[0]
    assert isinstance(added_role, UserRole)
    assert added_role.user_id == sub
    assert added_role.role_id == RoleRef.RESEARCHER.value
    assert mock_session.commit.call_count == 2


def test_ensure_user_and_role_is_idempotent_when_grant_already_exists(mock_session, idp):
    """Provider user exists and already has the role → no add, no commit."""
    sub = uuid4()
    idp.get_user.return_value = CognitoUser(id=sub, email="alex@example.com", is_disabled=False)  # type: ignore[call-arg]
    mock_session.exec.return_value.first.return_value = UserRole(user_id=sub, role_id=RoleRef.RESEARCHER.value)

    mock_session.get.return_value = UserProfile(user_id=sub, name="Alex Example", organisation="Example Org")

    ensure_user_and_role(idp, "alex@example.com", RoleRef.RESEARCHER, mock_session, "Alex Example", "Example Org")

    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()


def test_ensure_user_and_role_updates_existing_profile_when_seed_fields_drift(mock_session, idp):
    """A profile row already exists but the seeded display name/org have
    changed (e.g. a hardcoded admin was renamed in MAIN_USER_PROFILES).
    The profile must be brought in line on next boot.

    The role grant stays unchanged — `has_any_role` is short-circuited by the
    existing UserRole row.
    """
    sub = uuid4()
    idp.get_user.return_value = CognitoUser(id=sub, email="alex@example.com", is_disabled=False)  # type: ignore[call-arg]
    existing_profile = UserProfile(user_id=sub, name="Stale Name", organisation="Old Org")
    mock_session.get.return_value = existing_profile
    mock_session.exec.return_value.first.return_value = UserRole(user_id=sub, role_id=RoleRef.RESEARCHER.value)

    ensure_user_and_role(idp, "alex@example.com", RoleRef.RESEARCHER, mock_session, "Fresh Name", "New Org")

    # The in-place mutation + add() captures the updated row for the commit.
    assert existing_profile.name == "Fresh Name"
    assert existing_profile.organisation == "New Org"
    mock_session.add.assert_called_once_with(existing_profile)
    mock_session.commit.assert_called_once()
