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

"""The OMOP backend seam (FLIP#13).

One ``OmopBackend`` per place a trust's OMOP can live. The backend owns every fact about
the target that the rest of the service used to hardcode for PostgreSQL:

- how to build the SQLAlchemy engine (and with which pooling behaviour),
- which **sqlglot dialect** the validated cohort AST is emitted in,
- which **dangerous surface** the cohort validator allowlists against, and whether a
  three-part ``catalog.schema.table`` reference is a confusion attempt or the normal form,
- how a missing-table / missing-column error is recognised **without importing psycopg2**,
- how a result column name is folded back to the canonical lower case, and
- the dialect-specific SQL for the one thing OMOP's CDM cannot express portably: whole
  years of age.

The hub keeps fanning out ONE cohort SQL string, authored in the Postgres dialect. The
trust parses it as Postgres, validates it, and emits it in *this* backend's dialect, so no
per-trust query authoring and no hub change is needed.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from sqlalchemy import Engine, create_engine

from data_access_api.config import OMOP_BACKEND_SNOWFLAKE, Settings, get_settings
from data_access_api.utils.logger import logger
from data_access_api.utils.sql_parsers import extract_missing_identifier

# What ``classify_error`` reports: which kind of identifier the engine said was missing.
MissingIdentifier = tuple[Literal["table", "column"], str]


class OmopBackend(ABC):
    """One place a trust's OMOP CDM can live, and everything dialect-specific about it."""

    #: Short name, as carried by ``OMOP_BACKEND`` and reported by ``/health/omop``.
    name: str
    #: sqlglot dialect the validated cohort AST is EMITTED in. The hub always authors in
    #: ``postgres`` (see ``SOURCE_DIALECT`` in services.cohort), so this is the transpile target.
    sqlglot_dialect: str
    #: Set-returning functions permitted in a FROM clause. Allowlist, never a denylist.
    allowed_table_functions: frozenset[str]
    #: Functions permitted anywhere an expression is. Superset of the table functions.
    allowed_functions: frozenset[str]

    @abstractmethod
    def create_engine(self) -> Engine:
        """Build the SQLAlchemy engine for this backend.

        Returns:
            Engine: A configured engine with statement logging disabled (cohort SQL and its
            bind parameters carry patient identifiers and must never reach stdout).
        """

    @abstractmethod
    def allows_catalog(self, catalog: str) -> bool:
        """Whether a three-part ``catalog.schema.table`` reference may name this catalog.

        Args:
            catalog (str): The leading part of the reference, as the validator folded it.

        Returns:
            bool: True when the reference is pinned to this backend's own catalog.
        """

    @abstractmethod
    def pinned_catalog(self) -> str | None:
        """The catalog an unqualified table reference is pinned to, if the backend has one.

        Returns:
            str | None: The catalog name, or None when the backend has no cross-database
            namespace to pin (PostgreSQL).
        """

    def reject_table_reference(self, name: str) -> str | None:
        """A backend-specific reason this table name must be refused, if any.

        Args:
            name (str): The table's own name, as folded by the validator.

        Returns:
            str | None: The refusal reason, or None when the name is acceptable.
        """
        return None

    @abstractmethod
    def classify_error(self, orig: BaseException | None, message: str) -> MissingIdentifier | None:
        """Recognise a missing-table / missing-column error from this backend's driver.

        Args:
            orig (BaseException | None): The DBAPI exception SQLAlchemy wrapped.
            message (str): ``str(orig)``, stripped.

        Returns:
            MissingIdentifier | None: ``("table" | "column", identifier)`` when the error is
            one of those two diagnostics, else None (the caller answers an opaque category).
        """

    @abstractmethod
    def age_years_sql(self, column: str) -> str:
        """SQL for a person's age in whole years, in this backend's dialect.

        OMOP's CDM holds a birth datetime, not an age, and the two engines spell the
        difference differently: PostgreSQL has ``AGE()``, which Snowflake does not implement
        at all. Both forms must yield the SAME integer so ``/cohort`` statistics are
        backend-independent in value as well as shape.

        Args:
            column (str): The birth-datetime column reference, e.g. ``p.birth_datetime``.

        Returns:
            str: An expression evaluating to completed years of age.
        """

    def result_column_name(self, name: object) -> str:
        """Fold one result column name to its canonical form.

        PostgreSQL lower-folds unquoted identifiers, so ``SELECT accession_id`` already comes
        back as ``accession_id``; Snowflake upper-folds them and returns ``ACCESSION_ID``.
        Consumers (``flip.get_dataframe()``, the tutorial transform chains, and
        ``routers/cohort.py``'s own ``df[ACCESSION_ID_COLUMN]``) index in lower case, so the
        Snowflake backend folds back. A name that is not entirely upper case came from a
        QUOTED identifier and is left exactly as the engine returned it — which is what
        PostgreSQL does too, so the rule is one rule, not two.

        Args:
            name (object): The column label pandas returned.

        Returns:
            str: The canonical column name.
        """
        return str(name)

    def normalise_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Rename a result frame's columns through :meth:`result_column_name`.

        Args:
            df (pd.DataFrame): The frame as the driver returned it.

        Returns:
            pd.DataFrame: The same frame with canonical column names.
        """
        df.columns = pd.Index([self.result_column_name(column) for column in df.columns])
        return df


class PostgresOmopBackend(OmopBackend):
    """The mocked/on-prem PostgreSQL OMOP — the shipped default, unchanged by FLIP#13."""

    name = "postgres"
    sqlglot_dialect = "postgres"

    # An unqualified table-valued function carries no name and no schema on its exp.Table
    # node, so the schema pin cannot reach it — and Postgres resolves it through the implicit
    # pg_catalog, the same route the pin exists to close. There is no schema to pin it to
    # (these live in pg_catalog, not omop), so the only available control is an allowlist.
    # Keep it an allowlist, never a denylist of dangerous names: ``query_to_xml`` executes a
    # SQL string that sqlglot never parses, so it bypasses every other rule in validate_query,
    # and the next such function is one Postgres release away.
    allowed_table_functions = frozenset({"generate_series", "unnest"})
    # ``age`` is allowed because deriving patient age is an ordinary cohort filter and sqlglot
    # happens to leave it Anonymous.
    allowed_functions = allowed_table_functions | frozenset({"age"})

    def create_engine(self) -> Engine:
        """Build the psycopg2 engine, exactly as before the backend seam existed."""
        return create_engine(get_settings().OMOP_DATABASE_URL.get_secret_value(), echo=False)

    def allows_catalog(self, catalog: str) -> bool:
        """Never: Postgres has no cross-database references, so this can only be confusion."""
        return False

    def pinned_catalog(self) -> str | None:
        """None — there is no catalog namespace to pin on PostgreSQL."""
        return None

    def classify_error(self, orig: BaseException | None, message: str) -> MissingIdentifier | None:
        """Recognise psycopg2's ``UndefinedTable`` / ``UndefinedColumn``.

        Imported lazily so this module stays importable on a trust that has no psycopg2 in its
        image — the error path must not be the thing that binds the service to one driver.
        """
        try:
            from psycopg2 import errors as pg_errors
        except ImportError:  # pragma: no cover - psycopg2 ships with this service
            return None
        if isinstance(orig, pg_errors.UndefinedTable):
            return ("table", extract_missing_identifier(message, r'relation "([^"]+)" does not exist'))
        if isinstance(orig, pg_errors.UndefinedColumn):
            return ("column", extract_missing_identifier(message, r'column "([^"]+)" does not exist'))
        return None

    def age_years_sql(self, column: str) -> str:
        """``AGE()`` in whole years — the historical expression, kept byte for byte."""
        return f"DATE_PART('year', AGE(CURRENT_DATE, {column}))"


class SnowflakeOmopBackend(OmopBackend):
    """A trust's OMOP living in its own Snowflake tenancy (FLIP#13)."""

    name = "snowflake"
    sqlglot_dialect = "snowflake"

    # Snowflake's dangerous surface is NOT Postgres's, and reusing the Postgres allowlist
    # would both admit nothing useful and say nothing true. What matters here parses
    # perfectly cleanly as an ordinary function call and lands in exp.Anonymous:
    #   - SYSTEM$… built-ins (SYSTEM$GET_PRIVATELINK_CONFIG, SYSTEM$EXTERNAL_TABLE_PIPE_STATUS)
    #   - TABLE(RESULT_SCAN(LAST_QUERY_ID())) — replays ANOTHER query's result set, so it
    #     reaches rows this validator never saw,
    #   - external functions and user-defined functions, which execute code off-warehouse,
    #   - stage references (``FROM @stage``), which read files rather than tables.
    # So the posture stays "reject every unrecognised function", and the allowlist holds only
    # what a cohort query legitimately needs: FLATTEN, Snowflake's set-returning unnest.
    # ``age`` is deliberately absent — Snowflake has no AGE(), so admitting it would turn a
    # clean 400 into a runtime failure inside the warehouse.
    allowed_table_functions = frozenset({"flatten"})
    allowed_functions = allowed_table_functions

    def create_engine(self) -> Engine:
        """Build the Snowflake engine with auto-suspend-aware pooling.

        ``pool_pre_ping`` and a bounded ``pool_recycle`` are load-bearing rather than
        cosmetic: a warehouse that auto-suspends drops idle sessions, so a pooled connection
        handed out after an idle period is dead and the first cohort query of the day would
        fail on a stale socket.
        """
        settings = get_settings()
        return create_engine(
            settings.SNOWFLAKE_DATABASE_URL,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=3600,
            connect_args=snowflake_connect_args(settings),
        )

    def allows_catalog(self, catalog: str) -> bool:
        """Only this trust's own configured database.

        On Snowflake ``DB.SCHEMA.TABLE`` is the normal form, so rejecting every three-part
        reference (as the Postgres rule does) would reject ordinary cohort SQL. The rule
        becomes "pin the catalog too" rather than "reject": a reference naming any OTHER
        database is still refused.
        """
        return catalog.strip('"').upper() == get_settings().SNOWFLAKE_DATABASE.upper()

    def pinned_catalog(self) -> str | None:
        """This trust's OMOP database, pinned onto every unqualified table reference."""
        return get_settings().SNOWFLAKE_DATABASE

    def reject_table_reference(self, name: str) -> str | None:
        """Refuse a stage reference.

        ``FROM @mystage`` reads staged FILES rather than a table. It carries a name, so the
        schema pin would simply qualify it and pass it through.
        """
        if name.startswith("@"):
            return "Stage references are not allowed."
        return None

    def classify_error(self, orig: BaseException | None, message: str) -> MissingIdentifier | None:
        """Recognise Snowflake's object/identifier diagnostics.

        Matched on the driver's error NUMBER where it is available and on the message shape
        otherwise, never on an ``isinstance`` against the connector's exception classes: the
        whole point of the seam is that the diagnostic path is not bound to one driver, and
        the connector raises the same ``ProgrammingError`` class for both cases anyway.
        """
        errno = getattr(orig, "errno", None)
        if errno == _SNOWFLAKE_OBJECT_DOES_NOT_EXIST or re.search(_OBJECT_MISSING_PATTERN, message):
            return ("table", extract_missing_identifier(message, _OBJECT_MISSING_PATTERN))
        if errno == _SNOWFLAKE_INVALID_IDENTIFIER or re.search(_INVALID_IDENTIFIER_PATTERN, message):
            return ("column", extract_missing_identifier(message, _INVALID_IDENTIFIER_PATTERN))
        return None

    def age_years_sql(self, column: str) -> str:
        """Completed years of age, matching PostgreSQL's ``AGE()`` value exactly.

        ``DATEDIFF('year', ...)`` on Snowflake counts calendar-year boundaries, so it reads
        one too high until the birthday has passed; the CASE subtracts that year back off.
        The result is completed years, the same integer ``DATE_PART('year', AGE(...))``
        returns on PostgreSQL, so the decade buckets on ``/cohort`` agree across backends.
        """
        return (
            f"(DATEDIFF('year', {column}, CURRENT_DATE) - CASE WHEN "
            f"(MONTH(CURRENT_DATE) < MONTH({column})) OR "
            f"(MONTH(CURRENT_DATE) = MONTH({column}) AND DAY(CURRENT_DATE) < DAY({column})) "
            "THEN 1 ELSE 0 END)"
        )

    def result_column_name(self, name: object) -> str:
        """Lower-fold an all-upper-case label; leave a quoted (mixed-case) one untouched."""
        label = str(name)
        return label.lower() if label.isupper() else label


# Snowflake driver error numbers for the two diagnostics worth echoing back. Kept as plain
# ints rather than imported constants so this module never needs the connector installed.
_SNOWFLAKE_OBJECT_DOES_NOT_EXIST = 2003
_SNOWFLAKE_INVALID_IDENTIFIER = 904

# Snowflake phrases these two as English text, the same way Postgres does. Matched
# case-insensitively (inline ``(?i)``, so the same pattern string serves both the match test
# and ``extract_missing_identifier``) because the connector has varied the capitalisation
# across releases.
_OBJECT_MISSING_PATTERN = r"(?i)Object '([^']+)' does not exist"
_INVALID_IDENTIFIER_PATTERN = r"(?i)invalid identifier '([^']+)'"


def snowflake_connect_args(settings: Settings) -> dict[str, Any]:
    """Resolve Snowflake authentication into DBAPI connect arguments.

    Two supported modes, in this order, and **no password mode at all**:

    1. **SPCS OAuth token.** A service running on Snowpark Container Services is handed a
       short-lived token at ``SNOWFLAKE_TOKEN_FILE`` (``/snowflake/session/token``) and
       connects back to its own account through ``SNOWFLAKE_HOST``. This is how an SPCS
       service is meant to authenticate: no credential is stored, and the token is read at
       connect time rather than cached, so a rotated token is picked up on the next connect.
    2. **Key-pair.** Off SPCS (a trust node reading a Snowflake OMOP over the internet) the
       service authenticates as ``SNOWFLAKE_USER`` with the PEM private key at
       ``SNOWFLAKE_PRIVATE_KEY_FILE``, converted to the DER form the connector wants.

    Args:
        settings (Settings): The resolved service settings.

    Returns:
        dict[str, Any]: Arguments for ``create_engine(connect_args=...)``.

    Raises:
        RuntimeError: When neither an OAuth token nor a private key is available, so there is
            no way to authenticate that does not involve a password.
    """
    args: dict[str, Any] = {
        "account": settings.SNOWFLAKE_ACCOUNT,
        "warehouse": settings.SNOWFLAKE_WAREHOUSE,
        "database": settings.SNOWFLAKE_DATABASE,
        "schema": settings.SNOWFLAKE_SCHEMA,
        "role": settings.SNOWFLAKE_ROLE,
        # Left False so an idle session is allowed to lapse with the warehouse rather than
        # being held open against an auto-suspending one. The real statement guard is the
        # warehouse's own STATEMENT_TIMEOUT_IN_SECONDS, set by the grant script.
        "client_session_keep_alive": False,
    }
    if settings.SNOWFLAKE_HOST:
        args["host"] = settings.SNOWFLAKE_HOST

    token = _read_spcs_token(settings.SNOWFLAKE_TOKEN_FILE)
    if token is not None:
        logger.info("Authenticating to Snowflake with the SPCS-provided OAuth token")
        args["authenticator"] = "oauth"
        args["token"] = token
        return args

    if settings.SNOWFLAKE_PRIVATE_KEY_FILE:
        logger.info(f"Authenticating to Snowflake as {settings.SNOWFLAKE_USER} with key-pair auth")
        args["user"] = settings.SNOWFLAKE_USER
        args["private_key"] = _load_private_key(
            settings.SNOWFLAKE_PRIVATE_KEY_FILE,
            settings.SNOWFLAKE_PRIVATE_KEY_PASSPHRASE.get_secret_value(),
        )
        return args

    raise RuntimeError(
        "No Snowflake credential available: expected the SPCS OAuth token at "
        f"{settings.SNOWFLAKE_TOKEN_FILE} or a key-pair via SNOWFLAKE_PRIVATE_KEY_FILE. "
        "Password authentication is deliberately not supported."
    )


def _read_spcs_token(path: str) -> str | None:
    """Read the SPCS session token, or None when this service is not running on SPCS.

    Args:
        path (str): Token file path.

    Returns:
        str | None: The token, or None when the file is absent or empty.
    """
    token_file = Path(path)
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def _load_private_key(path: str, passphrase: str) -> bytes:
    """Load a PEM private key and return it in the DER form the connector expects.

    Args:
        path (str): PEM file path.
        passphrase (str): Key passphrase, or an empty string for an unencrypted key.

    Returns:
        bytes: The DER-encoded PKCS#8 private key.
    """
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(
        Path(path).read_bytes(),
        password=passphrase.encode() if passphrase else None,
    )
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


_BACKENDS: dict[str, type[OmopBackend]] = {
    PostgresOmopBackend.name: PostgresOmopBackend,
    SnowflakeOmopBackend.name: SnowflakeOmopBackend,
}

_backend: OmopBackend | None = None


def get_backend() -> OmopBackend:
    """The configured OMOP backend, built once and reused.

    Resolved lazily rather than at import so a test (and a future per-request override) can
    select a backend without the module having already committed to one.

    Returns:
        OmopBackend: The backend named by ``OMOP_BACKEND``.
    """
    global _backend
    if _backend is None:
        name = get_settings().OMOP_BACKEND
        _backend = _BACKENDS[name]()
        logger.info(f"OMOP backend: {name}")
    return _backend


def reset_backend() -> None:
    """Forget the cached backend, so the next call re-reads ``OMOP_BACKEND``."""
    global _backend
    _backend = None


def is_snowflake() -> bool:
    """Whether the configured backend is Snowflake.

    Returns:
        bool: True on the Snowflake backend.
    """
    return get_settings().OMOP_BACKEND == OMOP_BACKEND_SNOWFLAKE
