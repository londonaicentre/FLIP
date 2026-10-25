# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the e2e smoke's --gpus override (FLIP#70) and its token refresh against a remote hub."""

import argparse
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests import e2e_smoke
from tests.e2e_smoke import _parse_gpus, initiate_training, parse_args

TRUSTS = [{"id": "id-1", "name": "Azure Trust", "code": "AZ1"}]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", {"num_gpus": 0}),
        ("1", {"num_gpus": 1}),
        ("2", {"num_gpus": 2}),
    ],
)
def test_parse_gpus(value: str, expected: dict[str, int]) -> None:
    assert _parse_gpus(value) == expected


@pytest.mark.parametrize("value", ["", "one", "1:16", "1.5"])
def test_parse_gpus_rejects_malformed(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="expected a whole number of GPUs"):
        _parse_gpus(value)


def test_gpus_flag_defaults_to_no_override() -> None:
    required = ["--model-files-dir", "app"]
    assert parse_args(required).gpus is None
    assert parse_args([*required, "--gpus", "1"]).gpus == {"num_gpus": 1}


def _initiate(resources: dict[str, int] | None) -> dict[str, Any]:
    client = MagicMock()
    client.post.return_value = SimpleNamespace(status_code=204, text="")
    initiate_training(client, {}, "model-1", TRUSTS, resources)
    return client.post.call_args.kwargs["json"]


def test_initiate_sends_the_override_as_the_runs_resources() -> None:
    assert _initiate({"num_gpus": 0}) == {"trust_ids": ["id-1"], "resources": {"num_gpus": 0}}


def test_initiate_without_override_leaves_resources_out() -> None:
    # Absent, not null: the hub then falls back to the job's meta.json resource_spec or the FL API default.
    assert _initiate(None) == {"trust_ids": ["id-1"]}


def _keycloak_settings() -> SimpleNamespace:
    # What a checkout with .env.development present resolves to, whatever hub the smoke targets.
    return SimpleNamespace(
        AUTH_BACKEND="keycloak",
        KEYCLOAK_PUBLIC_URL="http://localhost:8180",
        KEYCLOAK_REALM="flip",
        KEYCLOAK_CLIENT_ID="flip-ui",
    )


def test_refresh_of_a_supplied_token_goes_to_cognito(monkeypatch: pytest.MonkeyPatch) -> None:
    """FLIP_E2E_TOKEN is a remote hub's Cognito token: the local Keycloak cannot renew it."""
    monkeypatch.setenv("FLIP_E2E_TOKEN", "old-access")
    monkeypatch.setenv("FLIP_E2E_REFRESH_TOKEN", "refresh")
    monkeypatch.setenv("AWS_COGNITO_APP_CLIENT_ID", "app-client")
    monkeypatch.setattr(e2e_smoke, "get_settings", _keycloak_settings)
    keycloak = MagicMock(side_effect=AssertionError("refreshed through the local Keycloak"))
    monkeypatch.setattr("flip_api.auth.identity.keycloak.refresh_grant", keycloak)
    cognito = MagicMock()
    cognito.initiate_auth.return_value = {"AuthenticationResult": {"AccessToken": "new-access"}}
    monkeypatch.setattr("boto3.client", MagicMock(return_value=cognito))

    headers = {"authorization": "Bearer old-access"}
    assert e2e_smoke._maybe_refresh(headers) is True

    assert headers["authorization"] == "Bearer new-access"
    cognito.initiate_auth.assert_called_once_with(
        ClientId="app-client", AuthFlow="REFRESH_TOKEN_AUTH", AuthParameters={"REFRESH_TOKEN": "refresh"}
    )


def test_refresh_of_a_local_login_still_goes_to_keycloak(monkeypatch: pytest.MonkeyPatch) -> None:
    """No FLIP_E2E_TOKEN: admin_authentication logged in against the dev Keycloak, which renews it."""
    monkeypatch.delenv("FLIP_E2E_TOKEN", raising=False)
    monkeypatch.setenv("FLIP_E2E_REFRESH_TOKEN", "refresh")
    monkeypatch.setattr(e2e_smoke, "get_settings", _keycloak_settings)
    keycloak = MagicMock(return_value={"access_token": "new-access", "refresh_token": "new-refresh"})
    monkeypatch.setattr("flip_api.auth.identity.keycloak.refresh_grant", keycloak)
    monkeypatch.setattr("boto3.client", MagicMock(side_effect=AssertionError("refreshed through Cognito")))

    headers = {"authorization": "Bearer old-access"}
    assert e2e_smoke._maybe_refresh(headers) is True

    assert headers["authorization"] == "Bearer new-access"
    keycloak.assert_called_once_with("http://localhost:8180", "flip", "flip-ui", "refresh")
