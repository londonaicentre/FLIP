# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Unit tests for the OMOP backend seam (FLIP#13).

No live Snowflake account exists and CI must not gain one, so the Snowflake path is
exercised with mocks: the URL and connect arguments the service would hand the driver, the
auth mode it picks, how it recognises the driver's diagnostics, and how it folds result
column names back to lower case. The dialect/validation half of the seam is pure sqlglot and
is covered in ``tests/services/test_cohort_dialects.py``.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from data_access_api.config import Settings
from data_access_api.db import backend as backend_module
from data_access_api.db.backend import (
    PostgresOmopBackend,
    SnowflakeOmopBackend,
    get_backend,
    reset_backend,
    snowflake_connect_args,
)

SNOWFLAKE_ENV = {
    "OMOP_BACKEND": "snowflake",
    "SNOWFLAKE_ACCOUNT": "ab12345",
    "SNOWFLAKE_WAREHOUSE": "FLIP_GSTT_WH",
    "SNOWFLAKE_DATABASE": "FLIP_TRUST_GSTT",
    "SNOWFLAKE_ROLE": "FLIP_GSTT_OMOP_READER",
}


def snowflake_settings(**overrides) -> Settings:
    """A fully configured Snowflake Settings instance.

    Args:
        **overrides: Settings fields to override.

    Returns:
        Settings: The settings a Snowflake trust's kit file would produce.
    """
    return Settings(**{**SNOWFLAKE_ENV, **overrides})


@pytest.fixture
def snowflake_backend(tmp_path):
    """The Snowflake backend bound to Snowflake settings, with no token file present."""
    settings = snowflake_settings(SNOWFLAKE_TOKEN_FILE=str(tmp_path / "absent-token"))
    with patch.object(backend_module, "get_settings", return_value=settings):
        yield SnowflakeOmopBackend(), settings


# ── Which backend the configuration selects ──────────────────────────────────────────


def test_the_default_backend_is_postgres():
    """An existing kit file carries no OMOP_BACKEND and must keep reading its local Postgres."""
    reset_backend()
    assert isinstance(get_backend(), PostgresOmopBackend)


def test_the_backend_is_resolved_once():
    reset_backend()
    assert get_backend() is get_backend()


def test_snowflake_is_selected_by_configuration_alone():
    """No code change and no per-trust image: the kit file is the whole switch."""
    reset_backend()
    try:
        with patch.object(backend_module, "get_settings", return_value=snowflake_settings()):
            assert isinstance(get_backend(), SnowflakeOmopBackend)
    finally:
        reset_backend()


# ── Connection ───────────────────────────────────────────────────────────────────────


def test_the_snowflake_url_names_account_database_schema_warehouse_and_role():
    url = snowflake_settings().SNOWFLAKE_DATABASE_URL

    assert url.drivername == "snowflake"
    assert url.host == "ab12345"
    assert url.database == "FLIP_TRUST_GSTT/OMOP"
    assert url.query["warehouse"] == "FLIP_GSTT_WH"
    assert url.query["role"] == "FLIP_GSTT_OMOP_READER"


def test_the_snowflake_url_carries_no_credential():
    """Credentials travel in connect_args, never in a URL string that could reach a log line."""
    url = snowflake_settings(SNOWFLAKE_USER="svc").SNOWFLAKE_DATABASE_URL

    assert url.password is None
    assert "token" not in url.render_as_string(hide_password=False)


def test_the_snowflake_schema_defaults_to_omop():
    assert snowflake_settings().SNOWFLAKE_SCHEMA == "OMOP"


@pytest.mark.parametrize("value", ["", None])
def test_an_empty_snowflake_schema_falls_back_to_omop(value):
    """A commented-out kit entry arrives as an empty string (the Makefile's sed export)."""
    assert snowflake_settings(SNOWFLAKE_SCHEMA=value).SNOWFLAKE_SCHEMA == "OMOP"


def test_the_snowflake_engine_pre_pings_and_recycles(snowflake_backend):
    """A warehouse that auto-suspends drops idle sessions, so a pooled connection goes stale."""
    backend, _ = snowflake_backend
    with (
        patch.object(backend_module, "create_engine") as create,
        patch.object(backend_module, "snowflake_connect_args", return_value={}),
    ):
        backend.create_engine()

    kwargs = create.call_args.kwargs
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] > 0
    assert kwargs["echo"] is False


def test_the_postgres_engine_is_unchanged():
    """Statement logging stays off: cohort SQL and its bind params carry patient identifiers."""
    with patch.object(backend_module, "create_engine") as create:
        PostgresOmopBackend().create_engine()

    assert create.call_args.kwargs == {"echo": False}
    assert create.call_args.args[0].startswith("postgresql+psycopg2://")


# ── Authentication: the SPCS token, then key-pair, and never a password ──────────────


def test_the_spcs_token_file_is_used_when_present(tmp_path):
    """How an SPCS service authenticates: a mounted token, no credential of its own."""
    token_file = tmp_path / "token"
    token_file.write_text("spcs-session-token", encoding="utf-8")
    settings = snowflake_settings(
        SNOWFLAKE_TOKEN_FILE=str(token_file),
        SNOWFLAKE_HOST="ab12345.us-east-1.snowflakecomputing.com",
    )

    args = snowflake_connect_args(settings)

    assert args["authenticator"] == "oauth"
    assert args["token"] == "spcs-session-token"
    assert args["host"] == "ab12345.us-east-1.snowflakecomputing.com"
    assert args["account"] == "ab12345"
    assert "password" not in args


def test_the_token_is_read_at_connect_time_not_cached(tmp_path):
    """SPCS rotates the token, so a cached copy would expire the service out of its own OMOP."""
    token_file = tmp_path / "token"
    token_file.write_text("first", encoding="utf-8")
    settings = snowflake_settings(SNOWFLAKE_TOKEN_FILE=str(token_file))

    assert snowflake_connect_args(settings)["token"] == "first"
    token_file.write_text("second", encoding="utf-8")
    assert snowflake_connect_args(settings)["token"] == "second"


def test_key_pair_auth_is_used_off_spcs(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_file = tmp_path / "key.pem"
    key_file.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    settings = snowflake_settings(
        SNOWFLAKE_TOKEN_FILE=str(tmp_path / "absent"),
        SNOWFLAKE_PRIVATE_KEY_FILE=str(key_file),
        SNOWFLAKE_USER="FLIP_GSTT_DATA_ACCESS_USER",
    )

    args = snowflake_connect_args(settings)

    assert args["user"] == "FLIP_GSTT_DATA_ACCESS_USER"
    assert isinstance(args["private_key"], bytes)
    assert "password" not in args
    assert "authenticator" not in args


def test_an_encrypted_private_key_is_opened_with_its_passphrase(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_file = tmp_path / "key.pem"
    key_file.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(b"secret"),
        )
    )
    settings = snowflake_settings(
        SNOWFLAKE_TOKEN_FILE=str(tmp_path / "absent"),
        SNOWFLAKE_PRIVATE_KEY_FILE=str(key_file),
        SNOWFLAKE_PRIVATE_KEY_PASSPHRASE="secret",  # pragma: allowlist secret
        SNOWFLAKE_USER="svc",
    )

    assert isinstance(snowflake_connect_args(settings)["private_key"], bytes)


def test_no_credential_at_all_refuses_rather_than_falling_back(tmp_path):
    """There is deliberately no password fallback to fall back TO."""
    settings = snowflake_settings(SNOWFLAKE_TOKEN_FILE=str(tmp_path / "absent"))

    with pytest.raises(RuntimeError, match="Password authentication is deliberately not supported"):
        snowflake_connect_args(settings)


def test_a_key_file_without_a_user_is_refused_at_import():
    """Key-pair auth names its user; a half-configured service must not boot."""
    with pytest.raises(ValueError, match="requires SNOWFLAKE_USER"):
        snowflake_settings(SNOWFLAKE_PRIVATE_KEY_FILE="/tmp/key.pem")


@pytest.mark.parametrize(
    "missing",
    ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_DATABASE", "SNOWFLAKE_ROLE"],
)
def test_an_incompletely_configured_snowflake_trust_refuses_to_start(missing):
    with pytest.raises(ValueError, match="OMOP_BACKEND=snowflake requires"):
        snowflake_settings(**{missing: ""})


@pytest.mark.parametrize(
    "missing",
    ["DATA_ACCESS_POSTGRES_USER", "DATA_ACCESS_POSTGRES_PASSWORD", "OMOP_POSTGRES_DB"],
)
def test_the_postgres_settings_are_still_required_on_the_postgres_backend(missing):
    """Making them optional for Snowflake must not stop a Postgres trust failing loudly."""
    with pytest.raises(ValueError, match="OMOP_BACKEND=postgres requires"):
        Settings(**{missing: ""})


# ── Error classification, not bound to psycopg2 ──────────────────────────────────────


def test_snowflake_missing_object_is_a_named_400(snowflake_backend):
    backend, _ = snowflake_backend
    error = MagicMock()
    error.errno = 2003

    assert backend.classify_error(
        error, "002003 (42S02): SQL compilation error:\nObject 'OMOP.PERSONN' does not exist or not authorized."
    ) == ("table", "OMOP.PERSONN")


def test_snowflake_invalid_identifier_is_a_named_400(snowflake_backend):
    backend, _ = snowflake_backend
    error = MagicMock()
    error.errno = 904

    assert backend.classify_error(
        error, "000904 (42000): SQL compilation error: error line 1 at position 7\ninvalid identifier 'PERSON_IDD'"
    ) == (
        "column",
        "PERSON_IDD",
    )


def test_snowflake_recognises_the_message_without_an_error_number(snowflake_backend):
    """The connector has not always carried ``errno``; the diagnostic must not depend on it."""
    backend, _ = snowflake_backend

    assert backend.classify_error(Exception(), "Object 'OMOP.NOPE' does not exist") == ("table", "OMOP.NOPE")


def test_an_unrecognised_snowflake_error_stays_a_category(snowflake_backend):
    """Everything else is category-only: driver text can carry row values."""
    backend, _ = snowflake_backend

    assert backend.classify_error(Exception(), "Warehouse 'FLIP_GSTT_WH' cannot be resumed") is None


def test_postgres_classification_is_unchanged():
    from psycopg2 import errors as pg_errors

    backend = PostgresOmopBackend()
    undefined_table = pg_errors.UndefinedTable()
    undefined_table.args = ('relation "omop.personn" does not exist',)

    assert backend.classify_error(undefined_table, str(undefined_table)) == ("table", "omop.personn")


def test_postgres_does_not_classify_a_snowflake_message():
    """Each backend recognises only its own driver, so a misconfiguration cannot be papered over."""
    assert PostgresOmopBackend().classify_error(Exception(), "Object 'X' does not exist") is None


# ── Result column names ──────────────────────────────────────────────────────────────


def test_snowflake_upper_folded_columns_come_back_lower_cased(snowflake_backend):
    """``routers/cohort.py`` indexes ``df[\"accession_id\"]`` in lower case, outside any try."""
    backend, _ = snowflake_backend
    df = pd.DataFrame({"ACCESSION_ID": ["a"], "PERSON_ID": [1]})

    assert list(backend.normalise_columns(df).columns) == ["accession_id", "person_id"]


def test_a_quoted_snowflake_column_keeps_its_case(snowflake_backend):
    """Quoted identifiers survive on Postgres too, so the rule is one rule, not two."""
    backend, _ = snowflake_backend
    df = pd.DataFrame({"Image date": ["2026-01-01"], "ACCESSION_ID": ["a"]})

    assert list(backend.normalise_columns(df).columns) == ["Image date", "accession_id"]


def test_postgres_column_names_are_untouched():
    df = pd.DataFrame({"accession_id": ["a"], "Image date": ["x"]})

    assert list(PostgresOmopBackend().normalise_columns(df).columns) == ["accession_id", "Image date"]


# ── Age in whole years, equal on both backends ───────────────────────────────────────


def test_postgres_age_expression_is_the_historical_one():
    assert PostgresOmopBackend().age_years_sql("p.birth_datetime") == (
        "DATE_PART('year', AGE(CURRENT_DATE, p.birth_datetime))"
    )


def test_the_snowflake_age_expression_avoids_the_nonexistent_age_function(snowflake_backend):
    backend, _ = snowflake_backend
    expression = backend.age_years_sql("p.birth_datetime")

    assert "AGE(" not in expression
    assert "DATEDIFF('year'" in expression
    # The CASE is what turns Snowflake's calendar-year difference into COMPLETED years, so the
    # decade buckets match Postgres exactly rather than approximately.
    assert "CASE WHEN" in expression


# ── The catalog rule ─────────────────────────────────────────────────────────────────


def test_postgres_rejects_every_catalog():
    """Postgres has no cross-database references, so a catalog can only be confusion."""
    assert PostgresOmopBackend().allows_catalog("otherdb") is False
    assert PostgresOmopBackend().pinned_catalog() is None


def test_snowflake_allows_only_its_own_database(snowflake_backend):
    backend, _ = snowflake_backend

    assert backend.allows_catalog("FLIP_TRUST_GSTT") is True
    assert backend.allows_catalog("flip_trust_gstt") is True
    assert backend.allows_catalog("FLIP_TRUST_OTHER") is False


def test_snowflake_pins_unqualified_tables_to_its_own_database(snowflake_backend):
    backend, _ = snowflake_backend

    assert backend.pinned_catalog() == "FLIP_TRUST_GSTT"


def test_snowflake_refuses_a_stage_reference(snowflake_backend):
    """``FROM @stage`` reads staged FILES; pinning it would merely qualify it."""
    backend, _ = snowflake_backend

    assert backend.reject_table_reference("@my_stage") == "Stage references are not allowed."
    assert backend.reject_table_reference("person") is None
