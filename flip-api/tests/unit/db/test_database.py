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

import importlib
import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event

from flip_api.db import database


def _fake_settings(**overrides: object) -> SimpleNamespace:
    """Build a settings stub with all attributes database.py reads."""
    base: dict[str, object] = {
        "ENV": "development",
        "POSTGRES_USER": "local_user",
        "POSTGRES_DB": "flip",
        "DB_HOST": "db.example.com",
        "DB_PORT": 5432,
        "AWS_REGION": "eu-west-2",
        "POSTGRES_PASSWORD": "devpass",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _reset_rds_client_cache():
    """Clear the module-level RDS client cache around every test."""
    database._rds_client = None
    yield
    database._rds_client = None


@pytest.fixture(autouse=True)
def _reset_engine_cache():
    """Clear the module-level lazy engine cache around every test."""
    database._engine = None
    yield
    database._engine = None


def test_import_performs_no_settings_read_or_engine_build():
    """Importing the module must not read settings or build an engine (FLIP#583).

    A fresh import is performed with ``get_settings`` booby-trapped so any
    import-time configuration read fails loudly — importers (tests, scripts,
    tooling) must not need env vars just to ``import flip_api.db.database``.
    """
    import flip_api.config as config
    import flip_api.db as db_pkg

    original_module = sys.modules["flip_api.db.database"]
    original_get_settings = config.get_settings

    def _boom() -> None:
        raise AssertionError("get_settings() was called at import time")

    try:
        config.get_settings = _boom
        del sys.modules["flip_api.db.database"]
        fresh = importlib.import_module("flip_api.db.database")
        assert fresh._engine is None
    finally:
        config.get_settings = original_get_settings
        sys.modules["flip_api.db.database"] = original_module
        db_pkg.database = original_module


def test_get_engine_builds_once_and_caches():
    """get_engine() builds the engine on first call and reuses it afterwards."""
    sentinel = object()
    with patch.object(database, "_build_engine", return_value=sentinel) as mock_build:
        first = database.get_engine()
        second = database.get_engine()

    assert first is sentinel
    assert second is sentinel
    mock_build.assert_called_once()


def test_get_session_uses_lazy_engine():
    """get_session() sessions are bound to the lazily-built cached engine."""
    with patch.object(database, "get_settings", return_value=_fake_settings(ENV="development")):
        generator = database.get_session()
        session = next(generator)

    assert database._engine is not None
    assert session.bind is database._engine
    generator.close()


def test_build_engine_dev_uses_env_password():
    """Dev embeds the env-var password in the URL and wires no IAM hook."""
    with patch.object(database, "get_settings", return_value=_fake_settings(ENV="development")):
        engine = database._build_engine()

    assert engine.url.username == "local_user"
    assert engine.url.password == "devpass"
    assert engine.url.host == "db.example.com"
    assert engine.url.database == "flip"
    assert not event.contains(engine, "do_connect", database._do_connect_listener)
    # The prod-only pooling knobs must not leak into the dev engine.
    assert engine.pool._pre_ping is False
    assert engine.pool._recycle == -1


def test_build_engine_dev_url_encodes_special_chars_in_password():
    """A dev password with space/'/'/'@'/'+' round-trips intact through the URL.

    quote_plus would encode the space as '+', which SQLAlchemy's URL parser
    decodes back as a literal '+', silently corrupting the password (the
    Copilot/FLIP#558 finding). quote(safe="") encodes it as %20 instead.
    """
    pw = "p@ss / word+1"
    with patch.object(database, "get_settings", return_value=_fake_settings(ENV="development", POSTGRES_PASSWORD=pw)):
        engine = database._build_engine()

    assert engine.url.password == pw


def test_build_engine_prod_omits_password_and_attaches_hook():
    """Prod builds a passwordless URL, enables pre-ping, and wires the do_connect hook."""
    with patch.object(database, "get_settings", return_value=_fake_settings(ENV="production")):
        engine = database._build_engine()

    assert engine.url.username == "local_user"
    assert engine.url.password is None
    assert engine.url.host == "db.example.com"
    assert engine.url.database == "flip"
    assert event.contains(engine, "do_connect", database._do_connect_listener)
    # pool_pre_ping is the staleness guard that lets a recycled/rotated
    # connection self-heal without a redeploy.
    assert engine.pool._pre_ping is True
    # pool_recycle must stay below the proxy's idle_client_timeout (1800s).
    assert engine.pool._recycle == database._POOL_RECYCLE_SECONDS


def test_build_engine_prod_requires_tls():
    """Prod passes sslmode=require to the driver — IAM auth mandates TLS."""
    real_create_engine = database.create_engine
    captured: dict[str, object] = {}

    def _spy(url, **kwargs):
        captured.update(kwargs)
        return real_create_engine(url, **kwargs)

    with (
        patch.object(database, "get_settings", return_value=_fake_settings(ENV="production")),
        patch.object(database, "create_engine", side_effect=_spy),
    ):
        database._build_engine()

    assert captured["connect_args"] == {"sslmode": "require"}


def test_do_connect_listener_injects_iam_token():
    """The do_connect hook sets the connection password to a freshly minted token."""
    cparams: dict[str, object] = {}
    with patch.object(database, "_generate_db_auth_token", return_value="iam-token-xyz"):
        database._do_connect_listener(object(), object(), [], cparams)

    assert cparams["password"] == "iam-token-xyz"


def test_do_connect_listener_mints_fresh_token_each_call():
    """The hook mints a new token on every call (no memoization) — the FLIP#556 guard."""
    first_params: dict[str, object] = {}
    second_params: dict[str, object] = {}
    with patch.object(database, "_generate_db_auth_token", side_effect=["tok-1", "tok-2"]) as mock_mint:
        database._do_connect_listener(object(), object(), [], first_params)
        database._do_connect_listener(object(), object(), [], second_params)

    assert mock_mint.call_count == 2
    assert first_params["password"] == "tok-1"
    assert second_params["password"] == "tok-2"


def test_generate_db_auth_token_calls_rds_client():
    """The token is minted via the RDS client with the configured host/user/region."""
    stt = _fake_settings(ENV="production")
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


def test_generate_db_auth_token_logs_and_reraises_on_failure(caplog):
    """A failed token mint logs non-secret context and re-raises the original error."""
    stt = _fake_settings(ENV="production")
    mock_client = MagicMock()
    mock_client.generate_db_auth_token.side_effect = RuntimeError("sts unavailable")

    with (
        patch.object(database, "get_settings", return_value=stt),
        patch("boto3.session.Session") as mock_session,
        caplog.at_level(logging.WARNING),
    ):
        mock_session.return_value.client.return_value = mock_client
        with pytest.raises(RuntimeError, match="sts unavailable"):
            database._generate_db_auth_token()

    assert "Failed to mint RDS IAM auth token" in caplog.text
    # Diagnostic context is logged so the failure is distinguishable from a
    # plain DB outage; the token and any secret are never logged. Assert against
    # the stub's attributes rather than host/user literals so the containment
    # check isn't misread as URL-substring sanitization (CodeQL CWE-020).
    assert stt.DB_HOST in caplog.text
    assert stt.POSTGRES_USER in caplog.text
    assert stt.AWS_REGION in caplog.text
    # exc_info=True preserves the original exception/traceback in the log record
    # so IAM-auth failures are diagnosable from CloudWatch (the FLIP#558 finding).
    assert "sts unavailable" in caplog.text


def test_get_rds_client_is_cached():
    """The RDS client is constructed once and reused across calls."""
    with (
        patch.object(database, "get_settings", return_value=_fake_settings(ENV="production")),
        patch("boto3.session.Session") as mock_session,
    ):
        first = database._get_rds_client()
        second = database._get_rds_client()

    assert first is second
    mock_session.return_value.client.assert_called_once()
