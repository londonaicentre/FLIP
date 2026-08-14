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

import datetime
from collections.abc import Mapping
from typing import Any

import pandas as pd
import sqlglot
from fastapi import HTTPException
from pandas.errors import DatabaseError as PandasDatabaseError
from psycopg2 import errors as pg_errors
from sqlalchemy import bindparam, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.sql.elements import TextClause
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import traverse_scope

from data_access_api.config import get_settings
from data_access_api.db.database import engine
from data_access_api.routers.schema import CohortQueryInput, StatisticsResponse
from data_access_api.services.query_cache import get_cached_result, set_cached_result
from data_access_api.utils.logger import logger
from data_access_api.utils.sql_parsers import extract_missing_identifier

# OMOP schema is the only schema callers may reference. Any qualified
# reference to a different schema is rejected by validate_query.
ALLOWED_SCHEMA = "omop"

# Set-returning functions permitted in a FROM clause. An unqualified table-valued function carries
# no name and no schema on its exp.Table node, so the schema pin cannot reach it — and Postgres
# resolves it through the implicit pg_catalog, the same route the pin exists to close. There is no
# schema to pin it to (these live in pg_catalog, not omop), so the only available control is an
# allowlist. Keep it an allowlist, never a denylist of dangerous names: ``query_to_xml`` executes a
# SQL string that sqlglot never parses, so it bypasses every other rule in validate_query, and the
# next such function is one Postgres release away.
_ALLOWED_TABLE_FUNCTIONS = frozenset({"generate_series", "unnest"})

# Functions permitted anywhere an expression is — select list, WHERE, a LATERAL FROM item, a
# subquery. A dangerous function is not confined to the FROM clause: the schema pin and the
# FROM-clause allowlist above both key on exp.Table nodes, so ``FROM LATERAL query_to_xml(...)``
# (an exp.Lateral, not an exp.Table), ``SELECT pg_read_file(...)`` and ``WHERE pg_ls_dir(...) IS
# NOT NULL`` all slipped past while Postgres still resolved them through the implicit pg_catalog.
# sqlglot models the ordinary cohort functions (COUNT, SUM, DATE_PART, COALESCE, SUBSTRING, ...) as
# typed nodes and wraps everything it does not recognise in exp.Anonymous — and the dangerous
# pg_catalog functions all land there: the query_to_xml family executes an unparsed SQL string,
# pg_read_file / pg_ls_dir / pg_stat_file read the server filesystem, current_setting / dblink /
# lo_import reach further still. So an unrecognised (Anonymous) function is rejected wherever it
# appears unless it is on this allowlist. This stays an allowlist for the same reason the table one
# does; the table functions are a subset (they are also legitimate in a lateral or scalar
# position), and ``age`` is included because deriving patient age is an ordinary cohort filter and
# sqlglot happens to leave it Anonymous. Every real cohort query in the tutorials uses only typed
# nodes, so the false-positive surface is small.
_ALLOWED_FUNCTIONS = _ALLOWED_TABLE_FUNCTIONS | frozenset({"age"})

# Reject pathologically large queries before sqlglot does any work — cheap DoS guard
# at the API layer. The DB role limits blast radius too, but rejecting here is
# defence in depth and stops the parser allocating an arbitrarily large AST.
MAX_QUERY_LENGTH = 10_240  # 10 KiB

# Top-level statement shapes that count as SELECT-like for the cohort API. This is an
# allowlist and must stay one — never add ``exp.Command``, sqlglot's catch-all for syntax it
# does not model (``EXPLAIN`` lands there, as does anything a future sqlglot stops
# understanding). A Command node round-trips the raw text verbatim and exposes no children,
# so the DML, schema and LIMIT/OFFSET walks below would all traverse nothing and pass it
# through unchecked.
_ALLOWED_QUERY_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.Union,
    exp.Intersect,
    exp.Except,
)

# Data-modifying nodes rejected anywhere in the tree, not just at the top level.
# Postgres allows a writable CTE — ``WITH x AS (DELETE ... RETURNING *) SELECT * FROM x``
# — which sqlglot parses with a top-level ``exp.Select``, so the SELECT-shape check
# alone passes it through. ``exp.Into`` is the ``INTO`` clause of ``SELECT ... INTO t``
# — ``CREATE TABLE AS`` in Postgres, but still an ``exp.Select`` carrying none of the
# four DML node types, so it needs its own entry here.
_DATA_MODIFYING_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Into,
)


def _invalid_query(detail: str) -> HTTPException:
    logger.warning(f"Query validation failed: {detail}")
    return HTTPException(status_code=400, detail=detail)


def _reject_disallowed_table_function(table: exp.Table) -> None:
    """Reject a function-shaped ``exp.Table`` whose function is not on the allowlist.

    Takes the name from the rendered call rather than the node: sqlglot models some functions as
    typed nodes whose ``.name`` is empty and whose ``sql_name()`` is an internal label
    (``generate_series`` renders from ExplodingGenerateSeries), while unrecognised ones are
    exp.Anonymous. Rendering gives the real Postgres name for both.

    Args:
        table (exp.Table): A FROM-clause item with an empty ``.name`` — the shape sqlglot gives a
            table-valued function call, qualified or not.

    Raises:
        HTTPException: 400 if the rendered function name is not in ``_ALLOWED_TABLE_FUNCTIONS``.
    """
    rendered = table.this.sql(dialect="postgres") if table.this else ""
    function_name = rendered.split("(", 1)[0].strip().lower()
    if function_name not in _ALLOWED_TABLE_FUNCTIONS:
        raise _invalid_query(
            f"'{function_name}' is not an allowed table-valued function. "
            f"Allowed: {', '.join(sorted(_ALLOWED_TABLE_FUNCTIONS))}."
        )


def validate_query(query: str) -> str:
    """
    Validates that an inbound SQL query is structurally safe to run against OMOP.

    Database-layer protections already in place
    -------------------------------------------
    The data-access-api connects as ``data_analyst_reader`` (see
    ``trust/omop-db/files/create_readonly_users.sql``), a Postgres role with no write
    privileges: ``INSERT`` / ``UPDATE`` / ``DELETE`` / ``TRUNCATE`` / ``CREATE`` are
    never granted, and are explicitly REVOKEd on top. Any DDL or DML is therefore
    rejected by Postgres itself, so this function does NOT keyword-filter for ``DROP``
    / ``INSERT`` / ``UPDATE`` / etc. Rules 3 and 4 below still reject writes
    *structurally*, from the parsed tree rather than from a keyword scan, so a write
    fails in-hand with a clear 400 instead of as an opaque permission error from the
    engine.

    **Read scope is NOT guaranteed by the database role, and differs by deployment.**
    The Kubernetes trust chart grants the role ``pg_read_all_data``
    (``deploy/providers/kubernetes/templates/omop-db.yaml``) — SELECT on every table
    in every schema — while the Compose path grants only ``USAGE`` on ``omop`` plus
    ``SELECT`` on its tables. So rule 5 below is the *only* thing keeping a caller
    inside ``omop`` on a Kubernetes trust, not a redundant second layer over a narrow
    grant. Do not weaken it on the assumption the role is scoped. Narrowing that grant
    to match Compose is tracked separately in FLIP#904; until it lands, this is the barrier.

    What this function enforces
    ---------------------------
    Since the read-only role can still issue arbitrary ``SELECT`` queries:

    1. The query is shorter than ``MAX_QUERY_LENGTH`` (DoS guard).
    2. The query parses as exactly one non-empty statement (defeats query stacking,
       stray semicolons that bypass the count check, and empty inputs).
    3. The top-level statement is SELECT-shaped (rejects ``COPY``, ``EXPLAIN``,
       and top-level DDL/DML — fail fast at the API rather than at the DB).
    4. No ``INSERT`` / ``UPDATE`` / ``DELETE`` / ``MERGE`` node appears *anywhere*
       in the tree. Rule 3 inspects only the top-level node, and Postgres allows a
       writable CTE — ``WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x``
       parses as a ``Select`` and would otherwise pass. The read-only role rejects
       the write regardless, so this is defence in depth, not the only barrier.
    5. Every table reference is pinned to the ``omop`` schema. A qualified
       reference to any other schema is rejected, as is a three-part
       ``db.schema.table`` reference; an *unqualified* reference is rewritten to
       ``omop.<table>`` in the AST before emission. Rewriting rather than
       trusting ``search_path`` is the load-bearing part: Postgres searches
       ``pg_catalog`` implicitly and first, whatever ``search_path`` says, so an
       unqualified ``pg_class`` would otherwise read the catalog — and on a
       Kubernetes trust the role can read every schema (see above). Names bound
       by a ``WITH`` clause are exempt because they are not schema-qualified and
       never can be. The exemption is determined from each lexical SQL scope, so
       a CTE in a nested query cannot exempt a table reference in its parent, and
       from Postgres-folded identifiers rather than raw text, so a quoted CTE
       name cannot exempt a reference that would not bind to it, and a quoted
       ``"OMOP"`` — a schema Postgres holds distinct from ``omop`` — is not
       read as the allowed one.

       A set-returning function in the ``FROM`` clause is checked against an
       allowlist instead. It carries no name and no schema to pin, and it
       resolves in ``pg_catalog`` rather than ``omop``, so pinning cannot
       express anything about it. The same allowlist is applied to every
       *unrecognised* function wherever it appears — a ``LATERAL`` function
       item, the select list, a ``WHERE`` predicate — since those positions
       reach ``pg_catalog`` too and are not ``exp.Table`` nodes for the pin to
       act on. ``query_to_xml`` and friends are the reason: they execute an
       unparsed SQL string and so bypass every other rule here.
    6. Every ``LIMIT`` and ``OFFSET`` is a literal integer (defeats the blind
       data-extraction technique that abuses
       ``LIMIT CASE WHEN <predicate> THEN n ELSE m END`` to make the row count
       a function of a single character value, then reads it back via the
       cohort-size error message).

    This function is the **authority** on cohort-query safety. The central hub
    runs its own pre-check before fanning a query out
    (``flip_api.cohort_services.submit_cohort_query.validate_query``), but that
    one exists purely for fast feedback and is deliberately weaker: the hub is a
    separate administrative domain, so nothing here may be relaxed on the
    assumption that the hub filtered first.

    Args:
        query: The SQL query string from the caller.

    Returns:
        The validated query re-emitted from its parsed AST — pass *this* to the
        database, never the caller's original string.

    Raises:
        HTTPException(400): When any of the rules above is violated, or when the validation
            pass itself fails on an input shape it does not handle.
    """
    try:
        return _validate_query_ast(query)
    except HTTPException:
        raise
    except Exception as e:
        # This function feeds adversarial input through a third-party parser; a shape the pass
        # mishandles must fail closed as a clean 400, not escape as an unhandled 500. Escaping
        # would also skip the re-emit at the end of the pass, so nothing is lost by rejecting.
        logger.exception("Query validation crashed on an unexpected input shape")
        raise _invalid_query("Could not validate query.") from e


def _validate_query_ast(query: str) -> str:
    """Single parse-validate-emit pass behind :func:`validate_query`.

    Kept separate so the public wrapper can convert anything unexpected escaping this pass into
    a fail-closed 400; the rule set is documented on :func:`validate_query`.
    """
    if len(query) > MAX_QUERY_LENGTH:
        raise _invalid_query(f"Query exceeds maximum length of {MAX_QUERY_LENGTH} characters.")

    try:
        statements = sqlglot.parse(query, read="postgres")
    except SqlglotError as e:
        # SqlglotError is the parent of both ParseError (e.g. "SELECT FROM") and
        # TokenError (e.g. unterminated string literals); catch the parent so a
        # tokenizer failure can't bubble up as an unhandled 500.
        raise _invalid_query(f"Could not parse SQL query: {e}") from e

    # sqlglot returns ``None`` for empty/whitespace input and for stray semicolons
    # (e.g. ``SELECT 1; ;`` parses to ``[Select, None]``). Reject if the result is
    # not exactly one non-empty statement — silently filtering ``None`` would let
    # callers smuggle an extra trailing semicolon past the single-statement check.
    if len(statements) != 1 or statements[0] is None:
        raise _invalid_query("Exactly one SQL statement is allowed per request.")

    stmt = statements[0]

    if not isinstance(stmt, _ALLOWED_QUERY_TYPES):
        raise _invalid_query("Only SELECT statements are allowed.")

    # The check above only inspects the top-level node, and Postgres lets a write
    # hide inside a CTE body while the outer statement still parses as a SELECT.
    # Walk the whole tree for data-modifying nodes.
    if any(stmt.find_all(*_DATA_MODIFYING_TYPES)):
        raise _invalid_query("Data-modifying statements are not allowed.")

    # Fold identifiers the way Postgres does — unquoted lowered, quoted left alone — before any
    # name comparison below. The CTE exemption matches ``scope.cte_sources`` keys against the
    # reference's literal text, and raw text is not what Postgres binds on, in both directions:
    #
    #   WITH "PG_CLASS" AS (...) SELECT relname FROM PG_CLASS
    #       raw text matches, so the reference was exempted -- but Postgres binds the CTE as
    #       PG_CLASS and folds the reference to pg_class, which it cannot bind, so the reference
    #       resolved through the implicit pg_catalog and skipped the pin entirely.
    #   WITH cohort AS (...) SELECT count(*) FROM COHORT
    #       raw text differs, so the reference was pinned to omop.COHORT -- but Postgres binds it
    #       to the CTE. omop.cohort is a real, empty OMOP CDM table, so this did not even fail
    #       loudly: the caller silently got a count of zero.
    #
    # Normalising first makes both sides agree with the engine. It is identity-preserving for
    # Postgres (an unquoted identifier is folded at parse time anyway), so the emitted SQL means
    # exactly what the caller wrote.
    stmt = normalize_identifiers(stmt, dialect="postgres")

    # A CTE reference and an unqualified physical table have the same exp.Table shape. Walk each
    # lexical scope so only a CTE visible from that exact SELECT is exempted; a flat set of every
    # CTE name would let an inner CTE exempt a physical table in an enclosing query.
    for scope in traverse_scope(stmt):
        for table in scope.tables:
            # Validate the schema FIRST, before any name-based skip. A schema-qualified
            # table-valued function — ``FROM pg_catalog.pg_ls_dir('/')`` — parses as an exp.Table
            # with an empty ``.name`` but a populated ``.db``, so skipping empty names ahead of
            # this check would step straight over it and admit a qualified non-omop reference.
            schema_node = table.args.get("db")
            if schema_node is not None:
                # ``catalog`` holds the leading part of a three-part db.schema.table reference,
                # which the schema check below does not see — ``otherdb.omop.person`` has db=omop
                # and passes it. Postgres has no cross-database references, so this can only be an
                # attempt to confuse the check; reject it rather than emit it. Checked first
                # because ``otherdb..person`` parses with catalog=otherdb and an *empty* ``db``,
                # and this is the rejection that actually describes that shape.
                if table.args.get("catalog") is not None:
                    raise _invalid_query("Cross-database table references are not allowed.")

                # ``db`` is usually an Identifier, but not always: sqlglot hands back a bare
                # ``str`` for degenerate shapes such as the empty schema slot above, so reading
                # ``.name`` unguarded used to escape as an AttributeError. Reject anything that
                # is not a plain Identifier outright.
                if not isinstance(schema_node, exp.Identifier):
                    raise _invalid_query("Malformed table reference.")

                # Compare the name as-is: ``normalize_identifiers`` above already folded it the
                # way Postgres does. Lowering here instead would re-open the same gap the
                # normalisation closes — ``"OMOP".person`` is a quoted identifier Postgres keeps
                # distinct from ``omop``, so a lowered comparison calls it allowed and then emits
                # it untouched, approving one schema while the engine reads another.
                schema_name = schema_node.name
                if schema_name != ALLOWED_SCHEMA:
                    raise _invalid_query(
                        f"Schema '{schema_name}' is not accessible. Only the '{ALLOWED_SCHEMA}' schema is allowed."
                    )

                # An empty name here is an *omop*-qualified table-valued function
                # (``FROM omop.pg_ls_dir('/')``): it passes the schema check by construction, and
                # the tree-wide exp.Anonymous walk below deliberately skips nodes an exp.Table
                # owns — so without this check it was the one spelling that reached the emit
                # without touching either allowlist.
                if not table.name:
                    _reject_disallowed_table_function(table)

                # Already qualified to omop — nothing to pin.
                continue

            table_name = table.name
            if not table_name:
                # An empty name with no schema is an unqualified table-valued function
                # (``FROM generate_series(1, 10)``), which sqlglot wraps in an exp.Table carrying
                # no name and no schema. There is nothing to pin — these resolve in pg_catalog,
                # not omop — so the allowlist is the only control available here. Skipping instead
                # would leave the FROM-clause function shape as a hole straight through every rule
                # above (FLIP#879).
                _reject_disallowed_table_function(table)
                continue

            if table_name in scope.cte_sources:
                continue

            # Unqualified. Postgres always searches pg_catalog — implicitly ahead of the
            # search_path schemas unless search_path names it explicitly, in which case at that
            # position. Either way it is always on the effective path, so ``FROM pg_class`` reads
            # the catalog however the database is configured (FLIP#879). Pin the reference to omop
            # in the tree rather than trusting resolution order: the SQL that reaches the engine is
            # emitted from this same tree at the end of this function, so a pinned node is a pinned
            # query. ``omop.pg_class`` does not exist and fails as a clean undefined-table error.
            #
            # Pinning rather than rejecting is deliberate — unqualified references are ordinary in
            # the cohort queries researchers write, and rejecting them would break saved queries to
            # close a hole that pinning closes completely.
            table.set("db", exp.to_identifier(ALLOWED_SCHEMA))

    # The scope walk above only reaches functions that sqlglot parsed into an exp.Table (a bare
    # ``FROM func(...)`` item). A dangerous function reached any other way — ``FROM LATERAL
    # func(...)`` parses as exp.Lateral, and select-list / WHERE / subquery calls parse as neither
    # — never touched a Table node, so it resolved through the implicit pg_catalog unchecked. Walk
    # every exp.Anonymous (sqlglot's node for an unrecognised function) tree-wide and allowlist it.
    # Skip the ones an exp.Table owns: those are FROM-clause table-valued functions already handled
    # above, and re-rejecting them here would only replace their specific message with this one.
    for function in stmt.find_all(exp.Anonymous):
        if isinstance(function.parent, exp.Table):
            continue
        function_name = function.name.lower()
        if function_name not in _ALLOWED_FUNCTIONS:
            raise _invalid_query(
                f"'{function_name}' is not an allowed function. "
                f"Allowed: {', '.join(sorted(_ALLOWED_FUNCTIONS))}."
            )

    for clause_type, label in ((exp.Limit, "LIMIT"), (exp.Offset, "OFFSET")):
        for node in stmt.find_all(clause_type):
            value = node.expression
            if not isinstance(value, exp.Literal) or not value.is_int:
                raise _invalid_query(f"{label} must be a literal integer.")

    # Re-emit from the AST we just validated rather than handing the caller's
    # original string to the engine. The string that reaches the database is
    # therefore generated by sqlglot from a checked tree, which breaks the
    # injection taint chain and incidentally normalises trailing semicolons and
    # whitespace. Emitting here — instead of in a second helper that re-parses —
    # keeps one parse and one policy: there is no second copy of the
    # single-statement and SELECT-shape rules to drift out of step with these.
    return stmt.sql(dialect="postgres")


def get_records(
    query: str | TextClause,
    params: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """
    Executes a SQL query and returns results.

    Args:
        query (str | TextClause): The SQL query to execute. Pass a ``TextClause`` with bind
            parameters when the query is parameterized.
        params (Mapping[str, Any] | None): Optional mapping of bind parameter names to values
            for parameterized queries.

    Returns:
        pd.DataFrame: The results of the query as a DataFrame.

    Raises:
        HTTPException: If the query is invalid or if there is an error during execution.
    """
    logger.info("Executing SQL query")

    cached = get_cached_result(query, params)
    if cached is not None:
        return cached

    try:
        # TODO: Trace the query filtering to understand what the final user can see.
        # Executing the query with pandas allows the user to query anything in the database.
        # This is a security risk, but since the input is a query we need to run it.
        # Therefore, we need to validate the query is safe, e.g. only SELECT queries, and does not contain sensitive
        # data.
        # TODO check if we can check column types -- could be used to exclude primary keys, foreign keys, etc.
        df = pd.read_sql(query, engine, params=params)
        set_cached_result(query, df, params)
        return df

    # Error responses are deliberately category-only (S-8): the trust forwards
    # this HTTPException detail to the central hub, which surfaces it through
    # the cohort UI to every project member. Raw psycopg / SQLAlchemy text can
    # leak row values, constraint names, and connection-pool internals — so we
    # log the full error here for ops and return only a category to the caller.
    # UndefinedTable / UndefinedColumn are an intentional exception: they echo
    # back identifiers the OPERATOR typed in their own SQL (against the public
    # OMOP CDM schema), so they leak no data while remaining a useful diagnostic.
    #
    # Pandas 3.x wraps SQLAlchemy errors in pandas.errors.DatabaseError (a subclass
    # of OSError, not SQLAlchemyError) with the original SQLAlchemy exception as
    # __cause__. We unwrap the cause here so the UndefinedTable / UndefinedColumn
    # diagnostic path still works when pd.read_sql is used directly.
    except PandasDatabaseError as e:
        cause = e.__cause__
        if isinstance(cause, DBAPIError):
            orig = cause.orig
            error_msg = str(orig).strip()
            if isinstance(orig, pg_errors.UndefinedTable):
                table_name = extract_missing_identifier(error_msg, r'relation "([^"]+)" does not exist')
                logger.error(f"UndefinedTable: {error_msg}")
                raise HTTPException(status_code=400, detail=f"The table '{table_name}' does not exist.") from e
            elif isinstance(orig, pg_errors.UndefinedColumn):
                column_name = extract_missing_identifier(error_msg, r'column "([^"]+)" does not exist')
                logger.error(f"UndefinedColumn: {error_msg}")
                raise HTTPException(status_code=400, detail=f"The column '{column_name}' does not exist.") from e
            else:
                logger.error(f"Database error (via pandas): {error_msg}")
                raise HTTPException(status_code=500, detail="query_failed") from e
        logger.error(f"Pandas database error: {str(e)}")
        raise HTTPException(status_code=500, detail="internal_error") from e

    except DBAPIError as e:
        orig = e.orig
        error_msg = str(orig).strip()

        if isinstance(orig, pg_errors.UndefinedTable):
            table_name = extract_missing_identifier(error_msg, r'relation "([^"]+)" does not exist')
            logger.error(f"UndefinedTable: {error_msg}")
            raise HTTPException(status_code=400, detail=f"The table '{table_name}' does not exist.") from e

        elif isinstance(orig, pg_errors.UndefinedColumn):
            column_name = extract_missing_identifier(error_msg, r'column "([^"]+)" does not exist')
            logger.error(f"UndefinedColumn: {error_msg}")
            raise HTTPException(status_code=400, detail=f"The column '{column_name}' does not exist.") from e

        else:
            logger.error(f"Database error: {error_msg}")
            raise HTTPException(status_code=500, detail="query_failed") from e

    except SQLAlchemyError as e:
        logger.error(f"SQLAlchemy error: {str(e)}")
        raise HTTPException(status_code=500, detail="internal_error") from e
    except Exception as e:
        logger.error(f"Unexpected error executing query: {str(e)}")
        raise HTTPException(status_code=500, detail="internal_error") from e


def get_counts(df: pd.DataFrame) -> dict:
    """
    Returns counts of non-null values for each column in the DataFrame.

    Args:
        df (pd.DataFrame): The cohort DataFrame.

    Returns:
        dict: ``{"name": "Counts", "results": [{"value": <column>, "count": <int>}, ...]}``.
    """
    return {
        "name": "Counts",
        "results": [{"value": col.replace("_", "\n"), "count": int(df[col].notnull().sum())} for col in df.columns],
    }


def get_null_counts(df: pd.DataFrame) -> dict:
    """
    Returns counts of null values for each column in the DataFrame.

    Args:
        df (pd.DataFrame): The cohort DataFrame.

    Returns:
        dict: ``{"name": "Nulls", "results": [{"value": <column>, "count": <int>}, ...]}``.
    """
    return {
        "name": "Nulls",
        "results": [{"value": col.replace("_", "\n"), "count": int(df[col].isnull().sum())} for col in df.columns],
    }


def get_sex_distribution(df: pd.DataFrame) -> dict:
    """
    Returns the distribution of sexes in the DataFrame.

    Assumes the DataFrame has accesion_id, query the table to get the sex distribution.

    Args:
        df (pd.DataFrame): The cohort DataFrame. Must include a ``person_id`` column; otherwise an
            empty result set is returned.

    Returns:
        dict: ``{"name": "Sex Distribution", "results": [{"value": <sex>, "count": <int>}, ...]}``.
    """
    if "person_id" not in df.columns:
        return {"name": "Sex Distribution", "results": []}

    person_ids = [int(pid) for pid in df["person_id"].unique()]

    sex_counts_database_query = text("""
    SELECT
    p.gender_source_value,
    COUNT(*) AS count
    FROM omop.person p
    WHERE p.person_id IN :person_ids
    GROUP BY p.gender_source_value
    """).bindparams(bindparam("person_ids", expanding=True))
    sex_counts = get_records(
        query=sex_counts_database_query,
        params={"person_ids": person_ids},
    )
    return {
        "name": "Sex Distribution",
        "results": [
            {"value": row["gender_source_value"], "count": int(row["count"])} for _, row in sex_counts.iterrows()
        ],
    }


def get_age_distribution(df: pd.DataFrame) -> dict:
    """
    Returns the distribution of ages in the DataFrame.

    Assumes the DataFrame has accesion_id, query the table to get the age distribution.

    Args:
        df (pd.DataFrame): The cohort DataFrame. Must include a ``person_id`` column; otherwise an
            empty result set is returned.

    Returns:
        dict: ``{"name": "Age Distribution", "results": [{"value": "<decade>", "count": <int>},
        ...]}`` where each value is a ten-year age bucket.
    """
    if "person_id" not in df.columns:
        return {"name": "Age Distribution", "results": []}

    person_ids = [int(pid) for pid in df["person_id"].unique()]

    age_distribution_database_query = text("""
    SELECT
    FLOOR(DATE_PART('year', AGE(CURRENT_DATE, p.birth_datetime)) / 10) * 10 AS age_group,
    COUNT(*) AS count
    FROM omop.person p
    WHERE p.person_id IN :person_ids
    GROUP BY age_group
    ORDER BY age_group
    """).bindparams(bindparam("person_ids", expanding=True))
    age_distribution = get_records(
        query=age_distribution_database_query,
        params={"person_ids": person_ids},
    )
    return {
        "name": "Age Distribution",
        "results": [
            {"value": f"{int(row['age_group'])}-{int(row['age_group']) + 9}", "count": int(row["count"])}
            for _, row in age_distribution.iterrows()
        ],
    }


def verify_cardinality(df: pd.DataFrame, threshold: float = 0.05) -> bool:
    """
    Verifies that the number of unique values in each column of the DataFrame is not smaller than the threshold.

    This is to prevent leaking information about individuals in the cohort.

    Args:
        df (pd.DataFrame): The cohort DataFrame to check.
        threshold (float): Minimum acceptable proportion of unique values per column. Defaults to
            ``0.05``.

    Returns:
        bool: ``True`` if every column has enough unique values (either above
        ``COHORT_QUERY_THRESHOLD`` in absolute terms, or above ``threshold`` in relative terms).
        ``False`` if any column falls below both thresholds.
    """
    for col in df.columns:
        unique_count = df[col].nunique()
        percentage_unique = unique_count / len(df) if len(df) > 0 else 0
        logger.info(f"Column '{col}' has {unique_count} unique values ({percentage_unique:.2%} of total)")
        if all([
            unique_count < get_settings().COHORT_QUERY_THRESHOLD,  # Absolute threshold
            percentage_unique < threshold,  # Relative threshold
        ]):
            logger.info(f"Column '{col}' has insufficient unique values ({threshold=}, {unique_count=})")
            return False
    return True


def make_other_category(results: list[dict], min_count: int | None = None) -> list[dict]:
    """
    Groups entries in the results list with counts less than min_count into an "Other" category.

    Args:
        results (list of dict): List of dictionaries with 'value' and 'count' keys.
        min_count (int | None): Minimum count threshold to avoid grouping into "Other".
            Defaults to ``COHORT_QUERY_THRESHOLD``, resolved at call time — a default
            argument would bind the setting at import and ignore a per-trust override.

    Returns:
        list of dict: Updated list with low-count entries grouped into "Other".
    """
    if min_count is None:
        min_count = get_settings().COHORT_QUERY_THRESHOLD

    other_count = sum(item["count"] for item in results if item["count"] < min_count)
    filtered_results = [item for item in results if item["count"] >= min_count]

    if other_count > 0:
        filtered_results.append({"value": "Other", "count": other_count})

    return filtered_results


def get_statistics(df: pd.DataFrame, query_input: CohortQueryInput, threshold: int) -> StatisticsResponse:
    """Returns aggregated statistics from the query results.

    - Counts the number of records.
    - Aggregates the number of occurrences of each unique value per column.

    Below-threshold counts are privacy-suppressed by returning a ``StatisticsResponse``
    with ``record_count=0``, empty ``data`` and ``suppressed=True`` — the count itself is
    suppressed, not just the per-field breakdown. A genuine zero is suppressed identically
    to a small (1..threshold-1) count, so the two are indistinguishable on the wire and the
    response cannot be used to infer that >=1 patient matched (membership disclosure — issue
    #519, security review). The ``suppressed`` flag only tells the hub/UI to render a
    "below-threshold" chip instead of a bare 0; it does not reveal which 0s were genuine.
    Suppression is intentional rather than an HTTPException so the trust still has a normal
    response to forward to the hub; raising here previously caused trust-api to skip the hub
    callback and leave the per-trust UI status stuck.

    Args:
        df (pd.DataFrame): Query results dataframe.
        query_input (data_access_api.routers.schema.CohortQueryInput): Input object containing the query and metadata.
        threshold (int): Minimum number of records the caller requires. ``COHORT_QUERY_THRESHOLD``
            is applied as a floor underneath it, so a caller can raise the bar but never
            lower it below the trust's configured disclosure threshold.

    Returns:
        StatisticsResponse: Contains the aggregated statistics, or a 0-count empty response
        when below the effective threshold.
    """
    record_count = len(df)
    # The configured threshold is a floor, not a default: a caller passing a smaller value
    # must not be able to weaken suppression. Read live rather than at import so a per-trust
    # override actually applies.
    threshold = max(threshold, get_settings().COHORT_QUERY_THRESHOLD)

    if record_count < threshold:
        # Privacy-suppress every below-threshold count, INCLUDING a genuine zero: a true
        # zero and a small (1..threshold-1) count return identically (record_count=0,
        # suppressed=True) so the response can't reveal that >=1 patient matched.
        # Distinguishing them would leak membership/existence (issue #519, security review).
        logger.info(
            f"Query returned {record_count} records (< {threshold});"
            " returning privacy-suppressed 0-count response"
        )
        return StatisticsResponse(
            query_id=query_input.query_id,
            trust_id=query_input.trust_id,
            record_count=0,
            created=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
            data=[],
            suppressed=True,
        )

    stats = StatisticsResponse(
        query_id=query_input.query_id,
        trust_id=query_input.trust_id,
        record_count=record_count,
        created=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
        data=[get_counts(df), get_null_counts(df)],
        suppressed=False,
    )

    if "person_id" in df.columns:
        logger.info("person_id column found in the query results; including age and sex distribution calculations.")
        age = get_age_distribution(df)
        age["results"] = make_other_category(age["results"], min_count=threshold)

        sex = get_sex_distribution(df)
        sex["results"] = make_other_category(sex["results"], min_count=threshold)

        stats.data += [age, sex]
    return stats
