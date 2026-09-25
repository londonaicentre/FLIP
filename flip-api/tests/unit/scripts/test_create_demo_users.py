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

"""``make demo-users``: demo accounts written through whichever identity provider is configured (FLIP#919).

The script plants known-password accounts, one of them an admin, so the
tests pin the two guards that matter — nothing is written without an
explicit interactive "yes" naming the resolved target, and passwords only
ever come from the environment — plus the idempotent re-run.
"""

from unittest.mock import MagicMock, patch

import pytest

from flip_api.auth.identity import IdentityProvider, IdentityProviderError, UserAlreadyExistsError
from flip_api.scripts import create_demo_users
from flip_api.utils.constants import DEMO_ADMIN_EMAIL, DEMO_RESEARCHER_EMAIL

MODULE = "flip_api.scripts.create_demo_users"


def _idp(backend: str = "keycloak") -> MagicMock:
    idp = MagicMock(spec=IdentityProvider)
    idp.backend = backend
    idp.describe_target.return_value = "Keycloak realm flip at http://keycloak:8080"
    return idp


@pytest.fixture
def passwords(monkeypatch):
    monkeypatch.setenv("DEMO_RESEARCHER_PASSWORD", "Researcher-Pa55!")  # pragma: allowlist secret
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "Admin-Pa55!")  # pragma: allowlist secret
    monkeypatch.delenv("FLIP_INSTANCE", raising=False)


# --- confirm_target ------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["yes", "  YES \n"])
def test_confirm_target_accepts_an_explicit_yes(capsys, answer):
    with patch("builtins.input", return_value=answer):
        assert create_demo_users.confirm_target(_idp()) is True
    assert "Keycloak realm flip at http://keycloak:8080" in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["", "y", "no"])
def test_confirm_target_treats_anything_but_yes_as_abort(capsys, answer):
    with patch("builtins.input", return_value=answer):
        assert create_demo_users.confirm_target(_idp()) is False
    assert "Aborted" in capsys.readouterr().err


def test_confirm_target_aborts_without_an_interactive_stdin(capsys):
    """Piped or CI invocations must not be able to confirm by accident."""
    with patch("builtins.input", side_effect=EOFError):
        assert create_demo_users.confirm_target(_idp()) is False
    assert "stdin closed" in capsys.readouterr().err


def test_a_cognito_target_also_names_the_aws_account(capsys):
    """A stale pool id or the wrong SSO session is only visible if the account is on screen."""
    idp = _idp("cognito")
    idp.describe_target.return_value = "Cognito user pool eu-west-2_TEST"
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": "000000000000"}
    with (
        patch("boto3.client", return_value=sts) as client,
        patch(f"{MODULE}.get_settings", return_value=MagicMock(AWS_REGION="eu-west-2")),
        patch("builtins.input", return_value="no"),
    ):
        create_demo_users.confirm_target(idp)
    client.assert_called_once_with("sts", region_name="eu-west-2")
    out = capsys.readouterr().out
    assert "Cognito user pool eu-west-2_TEST" in out
    assert "account: 000000000000" in out


def test_a_keycloak_target_makes_no_aws_call():
    with patch("boto3.client") as client, patch("builtins.input", return_value="no"):
        create_demo_users.confirm_target(_idp("keycloak"))
    client.assert_not_called()


# --- ensure_user ---------------------------------------------------------------------------


def test_ensure_user_creates_without_an_invite_then_sets_the_password():
    idp = _idp()
    create_demo_users.ensure_user(idp, "demo@example.com", "Pa55!")  # pragma: allowlist secret
    idp.create_user.assert_called_once_with("demo@example.com", suppress_invite=True)
    idp.set_password.assert_called_once_with("demo@example.com", "Pa55!")  # pragma: allowlist secret


def test_ensure_user_on_an_existing_user_only_resets_the_password(capsys):
    idp = _idp()
    idp.create_user.side_effect = UserAlreadyExistsError("exists")
    create_demo_users.ensure_user(idp, "demo@example.com", "Pa55!")  # pragma: allowlist secret
    idp.set_password.assert_called_once_with("demo@example.com", "Pa55!")  # pragma: allowlist secret
    assert "already exists" in capsys.readouterr().out


# --- main ----------------------------------------------------------------------------------


@pytest.mark.parametrize("unset", ["DEMO_RESEARCHER_PASSWORD", "DEMO_ADMIN_PASSWORD"])
def test_main_refuses_without_both_passwords_and_touches_no_provider(passwords, monkeypatch, capsys, unset):
    monkeypatch.delenv(unset)
    with patch(f"{MODULE}.build_identity_provider") as build:
        assert create_demo_users.main() == 1
    build.assert_not_called()
    assert "env-only" in capsys.readouterr().err


def test_main_writes_nothing_when_the_target_is_not_confirmed(passwords):
    idp = _idp()
    with patch(f"{MODULE}.build_identity_provider", return_value=idp), patch("builtins.input", return_value="no"):
        assert create_demo_users.main() == 1
    idp.create_user.assert_not_called()
    idp.set_password.assert_not_called()


def test_main_provisions_both_demo_users_and_names_the_restart(passwords, capsys):
    idp = _idp()
    with patch(f"{MODULE}.build_identity_provider", return_value=idp), patch("builtins.input", return_value="yes"):
        assert create_demo_users.main() == 0
    assert [c.args[0] for c in idp.create_user.call_args_list] == [DEMO_RESEARCHER_EMAIL, DEMO_ADMIN_EMAIL]
    idp.set_password.assert_any_call(DEMO_RESEARCHER_EMAIL, "Researcher-Pa55!")  # pragma: allowlist secret
    idp.set_password.assert_any_call(DEMO_ADMIN_EMAIL, "Admin-Pa55!")  # pragma: allowlist secret
    out = capsys.readouterr().out
    assert "docker compose -p deploy restart flip-api" in out
    # Passwords come from the environment and never go to the terminal.
    assert "Researcher-Pa55!" not in out  # pragma: allowlist secret
    assert "Admin-Pa55!" not in out  # pragma: allowlist secret


def test_main_names_the_instance_prefixed_project(passwords, monkeypatch, capsys):
    """Dev containers are named by compose project, so a second stack's restart hint must carry its prefix."""
    monkeypatch.setenv("FLIP_INSTANCE", "b")
    with patch(f"{MODULE}.build_identity_provider", return_value=_idp()), patch("builtins.input", return_value="yes"):
        assert create_demo_users.main() == 0
    assert "docker compose -p b-deploy restart flip-api" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("backend", "hint"),
    [("keycloak", "keycloak service is up"), ("cognito", "aws sso login")],
)
def test_main_turns_a_provider_error_into_a_backend_specific_hint(passwords, capsys, backend, hint):
    idp = _idp(backend)
    idp.set_password.side_effect = IdentityProviderError("Failed to set password")
    with (
        patch(f"{MODULE}.build_identity_provider", return_value=idp),
        patch(f"{MODULE}._describe_target", return_value="target"),
        patch("builtins.input", return_value="yes"),
    ):
        assert create_demo_users.main() == 1
    err = capsys.readouterr().err
    assert f"{backend} call failed: Failed to set password" in err
    assert hint in err
