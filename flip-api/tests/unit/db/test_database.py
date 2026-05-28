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
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event

from flip_api.db import database


def _fake_settings(**overrides: object) -> SimpleNamespace:
    """Build a settings stub with all attributes database.py reads."""
    base: dict[str, object] = {
        "ENV": "development",
        "DB_IAM_AUTH": False,
        "POSTGRES_USER": "local_user",
        "POSTGRES_DB": "flip",
        "DB_HOST": "db.example.com",
        "DB_PORT": 5432,
        "AWS_REGION": "eu-west-2",
        "POSTGRES_PASSWORD": "devpass",
        "POSTGRES_SECRET_ARN": "arn:aws:secretsmanager:eu-west-2:123456789012:secret:rds-db-abc",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _reset_rds_client_cache():
    """Clear the module-level RDS client cache around every test."""
    database._rds_client = None
    yield
    database._rds_client = None


def test_build_engine_dev_uses_env_password():
    """Dev (no IAM) embeds the env-var password in the URL."""
    with patch.object(database, "get_settings", return_value=_fake_settings(ENV="development", DB_IAM_AUTH=False)):
        engine = database._build_engine()

    assert engine.url.username == "local_user"
    assert engine.url.password == "devpass"
    assert engine.url.host == "db.example.com"
    assert engine.url.database == "flip"
    assert not event.contains(engine, "do_connect", database._do_connect_listener)


def test_build_engine_prod_password_path_reads_secret():
    """Legacy prod (IAM off) resolves the password from Secrets Manager."""
    stt = _fake_settings(ENV="production", DB_IAM_AUTH=False)
    with (
        patch.object(database, "get_settings", return_value=stt),
        patch.object(database, "get_secret", return_value="s3cret-from-asm") as mock_get_secret,
    ):
        engine = database._build_engine()

    mock_get_secret.assert_called_once_with(secret_key="password", secret_name=stt.POSTGRES_SECRET_ARN)
    assert engine.url.password == "s3cret-from-asm"


def test_build_engine_iam_omits_password_and_attaches_hook():
    """IAM mode builds a passwordless URL, enables pre-ping, and wires the do_connect hook."""
    stt = _fake_settings(ENV="production", DB_IAM_AUTH=True)
    with patch.object(database, "get_settings", return_value=stt):
        engine = database._build_engine()

    assert engine.url.username == "local_user"
    assert engine.url.password is None
    assert engine.url.host == "db.example.com"
    assert engine.url.database == "flip"
    assert event.contains(engine, "do_connect", database._do_connect_listener)
    # pool_pre_ping is the staleness guard that lets a recycled/rotated
    # connection self-heal without a redeploy.
    assert engine.pool._pre_ping is True


def test_do_connect_listener_injects_iam_token():
    """The do_connect hook sets the connection password to a freshly minted token."""
    cparams: dict[str, object] = {}
    with patch.object(database, "_generate_db_auth_token", return_value="iam-token-xyz"):
        database._do_connect_listener(object(), object(), [], cparams)

    assert cparams["password"] == "iam-token-xyz"


def test_generate_db_auth_token_calls_rds_client():
    """The token is minted via the RDS client with the configured host/user/region."""
    stt = _fake_settings(DB_IAM_AUTH=True)
    mock_client = MagicMock()
    mock_client.generate_db_auth_token.return_value = "iam-token"

    with (
        patch.object(database, "get_settings", return_value=stt),
        patch("boto3.session.Session") as mock_session,
    ):
        mock_session.return_value.client.return_value = mock_client
        token = database._generate_db_auth_token()

    assert token == "iam-token"
    mock_session.return_value.client.assert_called_once_with(service_name="rds", region_name="eu-west-2")
    mock_client.generate_db_auth_token.assert_called_once_with(
        DBHostname="db.example.com",
        Port=5432,
        DBUsername="local_user",
        Region="eu-west-2",
    )


def test_get_rds_client_is_cached():
    """The RDS client is constructed once and reused across calls."""
    stt = _fake_settings(DB_IAM_AUTH=True)
    with (
        patch.object(database, "get_settings", return_value=stt),
        patch("boto3.session.Session") as mock_session,
    ):
        first = database._get_rds_client()
        second = database._get_rds_client()

    assert first is second
    mock_session.return_value.client.assert_called_once()
