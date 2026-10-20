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
from sqlalchemy import bindparam, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.sql.elements import TextClause
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import traverse_scope

from data_access_api.config import get_settings
from data_access_api.db.backend import OmopBackend, get_backend
from data_access_api.db.database import get_engine
from data_access_api.routers.schema import CohortQueryInput, StatisticsResponse
from data_access_api.services.query_cache import get_cached_result, set_cached_result
from data_access_api.utils.logger import logger

# OMOP schema is the only schema callers may reference. Any qualified
# reference to a different schema is rejected by validate_query.
ALLOWED_SCHEMA = "omop"

# The dialect cohort SQL is AUTHORED in. The central hub fans out ONE query string to every
# trust in a project and every ``query.sql`` in this repo is written as PostgreSQL, so the
# source dialect is a constant and only the EMIT dialect follows the backend (FLIP#13).
# Parsing and identifier folding therefore stay Postgres-shaped on every backend, which is
# what keeps the ``omop`` schema pin and the CTE exemption comparing against the same folded
# names the author wrote. Snowflake upper-folds UNQUOTED identifiers itself, so the
# lower-case names sqlglot emits resolve to the upper-case OMOP objects, while a quoted
# identifier survives emission quoted and keeps its case on both backends.
SOURCE_DIALECT = "postgres"

# The dangerous-surface allowlists live on the BACKEND (``db/backend.py``), because the
# surface itself is backend-specific while the posture is not. Postgres's is about
# ``pg_catalog`` reachability (``query_to_xml``, ``pg_read_file``, ``dblink``); Snowflake's is
# about ``SYSTEM$...``, ``RESULT_SCAN(LAST_QUERY_ID())``, external functions and stages. What
# both share — and what makes this tractable — is the reject-unknown-by-default stance:
# sqlglot models the ordinary cohort functions (COUNT, SUM, DATE_PART, COALESCE, SUBSTRING,
# ...) as typed nodes and wraps everything it does not recognise in ``exp.Anonymous``, and
# every dangerous name lands there. So an unrecognised function is rejected wherever it
# appears — a FROM item, a LATERAL item, the select list, a WHERE predicate, a subquery —
# unless the backend's allowlist names it. The schema pin and the FROM-clause allowlist both
# key on ``exp.Table`` nodes, which is why the tree-wide Anonymous walk exists at all:
# ``FROM LATERAL query_to_xml(...)`` is an ``exp.Lateral``, and ``SELECT pg_read_file(...)``
# is neither. Keep each list an allowlist, never a denylist of dangerous names:
# ``query_to_xml`` executes a SQL string that sqlglot never parses, so it bypasses every
# other rule in validate_query, and the next such function is one release away.

# Cheapest possible "is the vocabulary there?" test: an existence probe, not a count. On a
# loaded trust concept_ancestor holds ~33M rows, so COUNT(*) would be a seq scan on the very
# path we are trying not to slow down.
_VOCABULARY_PROBE = text("SELECT 1 FROM omop.concept_ancestor LIMIT 1")


def _warn_if_vocabulary_missing() -> None:
    """Log an ERROR when a zero-row cohort is explained by an unloaded OMOP vocabulary.

    The trust-data seed loads only the free DICOM vocabulary; the licensed core vocabulary (which
    fills ``concept_ancestor``) is a separate, credentialed, ~25-minute step. Until it runs, every
    cohort query matches nothing while the imaging tables look perfectly healthy — so the failure
    reads as a bad query or a broken import rather than a missing core-vocabulary load.

    Deliberately best-effort and side-effect-free: this runs on a path that has already
    decided its response, so a failure here must never turn a valid privacy-suppressed answer
    into an error. Only called for a genuine zero — a below-threshold but non-zero cohort
    proves the vocabulary is fine.
    """
    try:
        with get_engine().connect() as connection:
            if connection.execute(_VOCABULARY_PROBE).first() is not None:
                return
    except SQLAlchemyError as exc:
        # Never escalate: the caller's response is already correct without this diagnosis.
        logger.debug(f"Could not check whether the OMOP vocabulary is loaded: {exc}")
        return

    logger.error(
        "Cohort query returned 0 records AND the OMOP vocabulary is not loaded "
        "(omop.concept_ancestor is empty). Cohort queries resolve concept sets through the "
        "vocabulary, so every query on this trust will match nothing until it is loaded. "
        "The licensed core vocabulary is loaded separately from the trust-data seed — run: "
        "make -C trust/omop-db load-omop-vocab OMOP_DB_PORT=<this trust's port>. "
        "Restart this service afterwards: query results are cached, so the 0-row answer would "
        "otherwise be replayed."
    )


# Reject pathologically large queries before sqlglot does any work — cheap DoS guard
# at the API layer. The DB role limits blast radius too, but rejecting here is
# defence in depth and stops the parser allocating an arbitrarily large AST.
MAX_QUERY_LENGTH = 10_240  # 10 KiB

# Columns a cohort can expose its subjects through, checked in this order by
# ``count_distinct_subjects``. ``accession_id`` matches the column the imaging pull selects and
# that the hub requires of every imaging project.
SUBJECT_ID_COLUMN = "person_id"
ACCESSION_ID_COLUMN = "accession_id"

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


def _reject_disallowed_table_function(table: exp.Table, backend: OmopBackend) -> None:
    """Reject a function-shaped ``exp.Table`` whose function is not on the backend's allowlist.

    Takes the name from the rendered call rather than the node: sqlglot models some functions as
    typed nodes whose ``.name`` is empty and whose ``sql_name()`` is an internal label
    (``generate_series`` renders from ExplodingGenerateSeries), while unrecognised ones are
    exp.Anonymous. Rendering gives the real authored name for both — rendered in the SOURCE
    dialect, because the allowlist judges what the author wrote, not what will be emitted.

    Args:
        table (exp.Table): A FROM-clause item with an empty ``.name`` — the shape sqlglot gives a
            table-valued function call, qualified or not.
        backend (OmopBackend): The configured OMOP backend, which owns the allowlist.

    Raises:
        HTTPException: 400 if the rendered function name is not allowlisted for this backend.
    """
    rendered = table.this.sql(dialect=SOURCE_DIALECT) if table.this else ""
    function_name = rendered.split("(", 1)[0].strip().lower()
    if function_name not in backend.allowed_table_functions:
        raise _invalid_query(
            f"'{function_name}' is not an allowed table-valued function. "
            f"Allowed: {', '.join(sorted(backend.allowed_table_functions))}."
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

    **Read scope: the ``omop`` schema, on both deployment paths — but do not lean on it.**
    Compose and the Kubernetes chart provision the role from the same
    ``create_readonly_users.sql`` (the chart runs the image's copy from its omop-db
    ``postStart`` hook, since a restored PVC skips initdb — FLIP#904; before that the
    chart granted ``pg_read_all_data``, SELECT on every table in every schema). Rule 5
    below is still kept as a full barrier in its own right: the grant comes from the
    *image's* copy of that file, so an image tag built before FLIP#904 leaves a
    Kubernetes role exactly as wide as it was (the hook logs a warning naming it), and
    an unqualified ``pg_catalog`` name resolves regardless of any schema grant. Do not
    weaken it on the assumption the role is scoped.

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
       reference to any other schema is rejected. A three-part
       ``db.schema.table`` reference is rejected on PostgreSQL (which has no
       cross-database references, so it can only be an attempt to confuse the
       check) and pinned to the trust's own OMOP database on Snowflake, where
       ``DB.SCHEMA.TABLE`` is the ordinary form; an *unqualified* reference is rewritten to
       ``omop.<table>`` in the AST before emission. Rewriting rather than
       trusting ``search_path`` is the load-bearing part: Postgres searches
       ``pg_catalog`` implicitly and first, whatever ``search_path`` says, so an
       unqualified ``pg_class`` would otherwise read the catalog, which no schema
       grant withholds (see above). Names bound
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

    Backends
    --------
    The rules and their guarantees are identical on every backend; the *implementation* is
    not, and must not be (FLIP#13). The query is always PARSED and identifier-folded as
    PostgreSQL — the dialect the hub authors in and every ``query.sql`` in this repo is
    written in — and EMITTED in the configured backend's dialect, so one string fanned out by
    the hub runs on a Postgres trust and a Snowflake trust alike. The dangerous-surface
    allowlist, the catalog rule and any backend-specific name shape come from
    ``db.backend.get_backend()``, because ``pg_catalog`` reachability and Snowflake's
    ``SYSTEM$…`` / ``RESULT_SCAN`` / stage surface are different problems with the same answer:
    reject what is not recognised.

    Returns:
        The validated query re-emitted from its parsed AST, in the backend's dialect — pass
        *this* to the database, never the caller's original string.

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
    backend = get_backend()

    if len(query) > MAX_QUERY_LENGTH:
        raise _invalid_query(f"Query exceeds maximum length of {MAX_QUERY_LENGTH} characters.")

    try:
        statements = sqlglot.parse(query, read=SOURCE_DIALECT)
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
    stmt = normalize_identifiers(stmt, dialect=SOURCE_DIALECT)

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
                # and passes it. Checked first because ``otherdb..person`` parses with
                # catalog=otherdb and an *empty* ``db``, and this is the rejection that actually
                # describes that shape.
                #
                # Whether a catalog is admissible at all is the backend's to answer. Postgres has
                # no cross-database references, so there it can only be an attempt to confuse the
                # check and is rejected outright. On Snowflake ``DB.SCHEMA.TABLE`` is the ordinary
                # form, so the rule becomes "pin the catalog too" rather than "reject": the
                # reference is allowed only when it names this trust's own OMOP database, and any
                # other database is refused exactly as any other schema is.
                catalog_node = table.args.get("catalog")
                if catalog_node is not None:
                    catalog_name = catalog_node.name if isinstance(catalog_node, exp.Identifier) else str(catalog_node)
                    if not backend.allows_catalog(catalog_name):
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
                    _reject_disallowed_table_function(table, backend)

                # Already qualified to omop — nothing to pin.
                continue

            table_name = table.name
            if table_name:
                # A backend-specific name shape the pin cannot express anything about — a
                # Snowflake stage reference (``FROM @stage``) reads staged FILES, not a table,
                # and carries a name, so pinning it would merely qualify it and pass it through.
                refusal = backend.reject_table_reference(table_name)
                if refusal is not None:
                    raise _invalid_query(refusal)
            if not table_name:
                # An empty name with no schema is an unqualified table-valued function
                # (``FROM generate_series(1, 10)``), which sqlglot wraps in an exp.Table carrying
                # no name and no schema. There is nothing to pin — these resolve in pg_catalog,
                # not omop — so the allowlist is the only control available here. Skipping instead
                # would leave the FROM-clause function shape as a hole straight through every rule
                # above (FLIP#879).
                _reject_disallowed_table_function(table, backend)
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
            # On a backend with a catalog namespace (Snowflake), pin that too: a session's
            # current database is a connection property, and pinning it in the tree means the
            # emitted query names the trust's own OMOP database explicitly rather than relying
            # on whatever database the session happens to be in.
            pinned_catalog = backend.pinned_catalog()
            if pinned_catalog is not None:
                table.set("catalog", exp.to_identifier(pinned_catalog))

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
        if function_name not in backend.allowed_functions:
            raise _invalid_query(
                f"'{function_name}' is not an allowed function. "
                f"Allowed: {', '.join(sorted(backend.allowed_functions))}."
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
    #
    # Emitted in the BACKEND's dialect, which is the whole transpilation hook (FLIP#13): the
    # hub authors one Postgres string, every trust parses and checks it identically, and each
    # emits it for its own engine. Because the string that reaches the database was always
    # generated from the checked tree rather than forwarded, transpiling here adds no new
    # trust in the caller's text.
    return stmt.sql(dialect=backend.sqlglot_dialect)


def get_records(
    query: str | TextClause,
    params: Mapping[str, Any] | None = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    Executes a SQL query and returns results.

    Args:
        query (str | TextClause): The SQL query to execute. Pass a ``TextClause`` with bind
            parameters when the query is parameterized.
        params (Mapping[str, Any] | None): Optional mapping of bind parameter names to values
            for parameterized queries.
        use_cache (bool): Whether a cached result may be served. Snapshot creation and the two
            row-level routes pass False: results are cached for ``CACHE_TTL_DAYS``, so a cached
            read would freeze, or keep serving, a patient or study since removed from OMOP (an
            opt-out). A fresh read still updates the cache afterwards.

    Returns:
        pd.DataFrame: The results of the query as a DataFrame.

    Raises:
        HTTPException: If the query is invalid or if there is an error during execution.
    """
    logger.info("Executing SQL query")

    if use_cache:
        cached = get_cached_result(query, params)
        if cached is not None:
            return cached

    backend = get_backend()

    try:
        # TODO: Trace the query filtering to understand what the final user can see.
        # Executing the query with pandas allows the user to query anything in the database.
        # This is a security risk, but since the input is a query we need to run it.
        # Therefore, we need to validate the query is safe, e.g. only SELECT queries, and does not contain sensitive
        # data.
        # TODO check if we can check column types -- could be used to exclude primary keys, foreign keys, etc.
        df = pd.read_sql(query, get_engine(), params=params)
        # Fold result column names to the canonical lower case BEFORE anything downstream sees
        # the frame, including the cache. Postgres lower-folds unquoted identifiers, Snowflake
        # upper-folds them, and every consumer indexes in lower case — ``routers/cohort.py``'s
        # ``df[ACCESSION_ID_COLUMN]``, ``flip.get_dataframe()`` callers, the tutorial transform
        # chains. On the Postgres backend this is the identity (nothing to fold), so the default
        # path is unchanged.
        df = backend.normalise_columns(df)
        set_cached_result(query, df, params)
        return df

    # Error responses are deliberately category-only (S-8): the trust forwards
    # this HTTPException detail to the central hub, which surfaces it through
    # the cohort UI to every project member. Raw driver / SQLAlchemy text can
    # leak row values, constraint names, and connection-pool internals — so we
    # log the full error here for ops and return only a category to the caller.
    # Missing-table / missing-column are an intentional exception: they echo
    # back identifiers the OPERATOR typed in their own SQL (against the public
    # OMOP CDM schema), so they leak no data while remaining a useful diagnostic.
    #
    # Recognising them is the BACKEND's job (``classify_error``), not an isinstance
    # against psycopg2's error classes: on a Snowflake trust there is no psycopg2 in the
    # call path, and binding the diagnostic to one driver is exactly what made these
    # degrade to an opaque 500 on any other (FLIP#13).
    #
    # Pandas 3.x wraps SQLAlchemy errors in pandas.errors.DatabaseError (a subclass
    # of OSError, not SQLAlchemyError) with the original SQLAlchemy exception as
    # __cause__. We unwrap the cause here so the diagnostic path still works when
    # pd.read_sql is used directly.
    except PandasDatabaseError as e:
        cause = e.__cause__
        if isinstance(cause, DBAPIError):
            raise _database_error(backend, cause.orig, e, via_pandas=True) from e
        logger.error(f"Pandas database error: {str(e)}")
        raise HTTPException(status_code=500, detail="internal_error") from e

    except DBAPIError as e:
        raise _database_error(backend, e.orig, e, via_pandas=False) from e

    except SQLAlchemyError as e:
        logger.error(f"SQLAlchemy error: {str(e)}")
        raise HTTPException(status_code=500, detail="internal_error") from e
    except Exception as e:
        logger.error(f"Unexpected error executing query: {str(e)}")
        raise HTTPException(status_code=500, detail="internal_error") from e


def _database_error(
    backend: OmopBackend,
    orig: BaseException | None,
    error: Exception,
    via_pandas: bool,
) -> HTTPException:
    """Map one driver error onto the HTTPException the caller should see.

    Args:
        backend (OmopBackend): The configured backend, which recognises its own driver's
            missing-table / missing-column diagnostics.
        orig (BaseException | None): The DBAPI exception SQLAlchemy wrapped.
        error (Exception): The exception being handled, for the log line.
        via_pandas (bool): Whether the error arrived wrapped by pandas, for the log line.

    Returns:
        HTTPException: A 400 naming the missing identifier, or a category-only 500.
    """
    error_msg = str(orig).strip()
    classified = backend.classify_error(orig, error_msg)
    if classified is not None:
        kind, identifier = classified
        logger.error(f"Missing {kind}: {error_msg}")
        return HTTPException(status_code=400, detail=f"The {kind} '{identifier}' does not exist.")
    logger.error(f"Database error{' (via pandas)' if via_pandas else ''}: {error_msg or str(error)}")
    return HTTPException(status_code=500, detail="query_failed")


def count_distinct_subjects(df: pd.DataFrame, use_cache: bool = True) -> int | None:
    """Returns how many distinct people a cohort covers, or None when that cannot be established.

    ``COHORT_QUERY_THRESHOLD`` is a floor on *individuals*: the suppression it drives exists so a
    response cannot reveal that ">=1 patient matched" (membership disclosure, issue #519). Counting
    rows only matched that intent while every cohort was one row per person, which stopped being
    true once a cohort could be one row per imaging *study* (a patient with ten X-rays is ten rows)
    and once tabular, imaging-free projects arrived (FLIP#1071). The row count remains an upper
    bound on the result: on the ``person_id`` path distinct values cannot exceed rows, and on the
    ``accession_id`` path the bound is applied explicitly, because nothing in the OMOP schema makes
    ``image_occurrence.accession_id`` functional on ``person_id`` (``varchar NULL``, no unique
    constraint), so three accession numbers could resolve to twelve people. Gating on the bounded
    count therefore replaces the row check rather than weakening it.

    Two ways to establish the count, in order of directness:

    - ``person_id`` in the projection — counted directly, no database round trip.
    - ``accession_id`` in the projection — resolved through ``omop.image_occurrence``. Imaging
      projects must already project this column (the hub 400s without it), so an imaging cohort is
      covered without asking researchers to expose ``person_id`` to the training dataframe.

    A cohort that resolves to neither returns None and the caller must fail closed: an
    unestablished count is not a small one, but it is not a safe one either.

    A projection that carries the subject column twice (``SELECT *`` over a join that keeps both
    sides' ``person_id``) is counted from the first copy rather than raising: ``df[column]`` yields a
    DataFrame in that case, and a raise here would surface as an opaque 500 on the training path.

    Args:
        df (pd.DataFrame): The cohort DataFrame.
        use_cache (bool): Whether the ``accession_id`` lookup may be served from the query cache.

    Returns:
        int | None: Distinct subjects covered, or None when the cohort exposes no column this can
        be derived from.
    """
    if SUBJECT_ID_COLUMN in df.columns:
        subjects = int(_first_column(df, SUBJECT_ID_COLUMN).nunique(dropna=True))
    elif ACCESSION_ID_COLUMN in df.columns:
        subjects = _subjects_behind_accessions(_first_column(df, ACCESSION_ID_COLUMN), use_cache=use_cache)
    else:
        return None
    return min(len(df), subjects)


def keep_imaging_accessions(df: pd.DataFrame, use_cache: bool = True) -> pd.DataFrame:
    """Returns the rows of an accession-id cohort whose value is a real imaging accession.

    ``/cohort/accession-ids`` counts its subjects through ``omop.image_occurrence``, so a value
    that resolves to no imaging study contributes nothing to the floor. Releasing such values
    anyway let a cohort smuggle arbitrary columns out under the ``accession_id`` alias — real
    accessions clearing the floor, and aliased person data riding along with them — past a
    ``cohort.dataframe`` deny (FLIP#1259). Only the values the floor counted are released. What a
    real imaging cohort can lose is an accession the trust's PACS holds but its
    ``omop.image_occurrence`` does not; that value never counted towards the floor either, so the
    route stops releasing what it was not gating.

    Args:
        df (pd.DataFrame): A frame whose ``accession_id`` column holds the candidate values.
        use_cache (bool): Whether the lookup may be served from the query cache.

    Returns:
        pd.DataFrame: The rows whose ``accession_id`` is in ``omop.image_occurrence``, in cohort
        order, duplicates kept.
    """
    column = _first_column(df, ACCESSION_ID_COLUMN)
    ids = [str(value) for value in column.dropna().unique()]
    if not ids:
        return df.iloc[0:0]

    lookup = text("""
    SELECT DISTINCT io.accession_id AS accession_id
    FROM omop.image_occurrence io
    WHERE io.accession_id IN :accession_ids
    """).bindparams(bindparam("accession_ids", expanding=True))
    result = get_records(query=lookup, params={"accession_ids": ids}, use_cache=use_cache)
    if ACCESSION_ID_COLUMN not in result.columns:
        # Cannot happen against a real database. Fails closed: nothing is known to be imaging.
        logger.error("Imaging accession lookup returned an unexpected shape; releasing no accession ids")
        return df.iloc[0:0]
    known = set(result[ACCESSION_ID_COLUMN].astype(str))
    return df[column.notna() & column.astype(str).isin(known)]


def _first_column(df: pd.DataFrame, column: str) -> pd.Series:
    """Returns ``df[column]`` as a Series even when the projection carries that column twice.

    Args:
        df (pd.DataFrame): The cohort DataFrame.
        column (str): The column name to select.

    Returns:
        pd.Series: The first column of that name.
    """
    selected = df[column]
    if isinstance(selected, pd.DataFrame):
        return selected.iloc[:, 0]
    return selected


def _subjects_behind_accessions(accession_ids: pd.Series, use_cache: bool = True) -> int:
    """Counts the distinct people the given accession numbers belong to.

    Accession ids are the pointer set into imaging, so ``omop.image_occurrence`` is authoritative
    for whose studies they are. An id that resolves to no row contributes no subject, which makes
    the count fail closed: a cohort whose accession numbers are not real imaging accessions (a
    tabular query aliasing some other column to that name, say) counts zero subjects and is refused
    rather than waved through on its row count.

    It is one aggregate over a list the caller already holds — a sequential scan today, since the
    OMOP DDL carries no index on ``image_occurrence.accession_id``.

    Args:
        accession_ids (pd.Series): The cohort's ``accession_id`` column.
        use_cache (bool): Whether the lookup may be served from the query cache.

    Returns:
        int: Distinct ``person_id`` values behind those accession numbers.
    """
    ids = [str(value) for value in accession_ids.dropna().unique()]
    if not ids:
        return 0

    subject_count_query = text("""
    SELECT COUNT(DISTINCT io.person_id) AS subject_count
    FROM omop.image_occurrence io
    WHERE io.accession_id IN :accession_ids
    """).bindparams(bindparam("accession_ids", expanding=True))
    result = get_records(query=subject_count_query, params={"accession_ids": ids}, use_cache=use_cache)
    if result.empty or "subject_count" not in result.columns:
        # Cannot happen against a real database: the aggregate above always yields one labelled
        # row. Guarded anyway so an unexpected shape fails closed as "no subjects established"
        # rather than raising a KeyError the routes would surface as an opaque 500.
        logger.error("Subject-count query returned an unexpected shape; treating it as no subjects")
        return 0
    return int(result["subject_count"].iloc[0])


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

    The person's standard Gender concept (8507 male, 8532 female), the CDM's required field, else its
    optional ``gender_source_value`` for a row whose concept is not one of those two.

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
    SELECT sex, COUNT(*) AS count
    FROM (
        SELECT COALESCE(
            CASE p.gender_concept_id WHEN 8507 THEN 'M' WHEN 8532 THEN 'F' END,
            NULLIF(p.gender_source_value, '')
        ) AS sex
        FROM omop.person p
        WHERE p.person_id IN :person_ids
    ) persons
    GROUP BY sex
    """).bindparams(bindparam("person_ids", expanding=True))
    sex_counts = get_records(
        query=sex_counts_database_query,
        params={"person_ids": person_ids},
    )
    return {
        "name": "Sex Distribution",
        "results": [{"value": row["sex"], "count": int(row["count"])} for _, row in sex_counts.iterrows()],
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

    # FLIP's own statistics SQL is raw text that never passes through validate_query, so it is
    # not transpiled either: the one expression OMOP's CDM cannot state portably — completed
    # years from a birth datetime — comes from the backend. PostgreSQL's ``AGE()`` does not
    # exist on Snowflake at all, so leaving it hardcoded failed /cohort at runtime there
    # (FLIP#13). Both forms yield the same integer, so the decade buckets agree across backends.
    age_distribution_database_query = text(f"""
    SELECT
    FLOOR({get_backend().age_years_sql("p.birth_datetime")} / 10) * 10 AS age_group,
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


def get_modality_distribution(df: pd.DataFrame) -> dict:
    """Returns how the cohort's imaging studies break down by modality.

    Only meaningful when the cohort actually covers imaging, which trust-side is exactly the
    question "does it project ``accession_id``". The trust never sees the project's ``has_imaging``
    flag (that is hub state), and it does not need to: a cohort with no accession numbers has no
    studies to describe.

    Counted in **studies**, not people, because modality is a property of an imaging study rather
    than of a patient: one person contributing a CT and an MR is one row in the age and sex
    distributions and two here. That is a different unit from its sibling charts on purpose.

    The concept join is a LEFT JOIN falling back to the raw ``modality_concept_id``, because a
    trust can be missing its OMOP vocabulary entirely (FLIP#967) and a readable label is worth
    less than an answer.

    Args:
        df (pd.DataFrame): The cohort DataFrame. Must include an ``accession_id`` column;
            otherwise an empty result set is returned.

    Returns:
        dict: ``{"name": "Modality Distribution", "results": [{"value": <modality>,
        "count": <int>}, ...]}``, ordered by descending count.
    """
    if ACCESSION_ID_COLUMN not in df.columns:
        return {"name": "Modality Distribution", "results": []}

    accession_ids = [str(value) for value in df[ACCESSION_ID_COLUMN].dropna().unique()]
    if not accession_ids:
        return {"name": "Modality Distribution", "results": []}

    modality_database_query = text("""
    SELECT
    COALESCE(c.concept_name, CAST(io.modality_concept_id AS VARCHAR)) AS modality,
    COUNT(DISTINCT io.image_occurrence_id) AS count
    FROM omop.image_occurrence io
    LEFT JOIN omop.concept c ON c.concept_id = io.modality_concept_id
    WHERE io.accession_id IN :accession_ids
    GROUP BY modality
    ORDER BY count DESC
    """).bindparams(bindparam("accession_ids", expanding=True))
    modalities = get_records(
        query=modality_database_query,
        params={"accession_ids": accession_ids},
    )
    if modalities.empty or not {"modality", "count"} <= set(modalities.columns):
        # Cannot happen against a real database (the GROUP BY always yields labelled rows, or none
        # only when no accession resolved). Logged so an operator has a trail when it does.
        logger.error("Modality query returned an unexpected shape; reporting no modalities")
        return {"name": "Modality Distribution", "results": []}

    return {
        "name": "Modality Distribution",
        "results": [{"value": row["modality"], "count": int(row["count"])} for _, row in modalities.iterrows()],
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
        if all(
            [
                unique_count < get_settings().COHORT_QUERY_THRESHOLD,  # Absolute threshold
                percentage_unique < threshold,  # Relative threshold
            ]
        ):
            logger.info(f"Column '{col}' has insufficient unique values ({threshold=}, {unique_count=})")
            return False
    return True


def make_other_category(results: list[dict], min_count: int | None = None) -> list[dict]:
    """
    Groups entries in the results list with counts less than min_count into an "Other" category.

    "Other" is itself held to the threshold: it is only emitted when the folded total is at least
    ``min_count``, and dropped otherwise. A single sub-threshold bucket folded alone would publish
    that bucket's exact count under a different label (``[CT: 300, Other: 4]`` says four studies
    carry the one other modality), and two small buckets summing below the floor are a
    sub-threshold count all the same. No count below the floor leaves this function, so the rule
    is the same for the modality, age and sex charts.

    Args:
        results (list[dict]): List of dictionaries with 'value' and 'count' keys.
        min_count (int | None): Minimum count threshold to avoid grouping into "Other".
            Defaults to ``COHORT_QUERY_THRESHOLD``, resolved at call time — a default
            argument would bind the setting at import and ignore a per-trust override.

    Returns:
        list[dict]: Updated list with low-count entries grouped into "Other", or dropped when
        even their total is below ``min_count``.
    """
    if min_count is None:
        min_count = get_settings().COHORT_QUERY_THRESHOLD

    other_count = sum(item["count"] for item in results if item["count"] < min_count)
    filtered_results = [item for item in results if item["count"] >= min_count]

    if other_count >= min_count:
        filtered_results.append({"value": "Other", "count": other_count})

    return filtered_results


def get_statistics(df: pd.DataFrame, query_input: CohortQueryInput, threshold: int) -> StatisticsResponse:
    """Returns aggregated statistics from the query results.

    - Counts the number of records.
    - Aggregates the number of occurrences of each unique value per column.
    - Adds the age and sex distributions when the cohort projects ``person_id``, and the modality
      distribution when it projects ``accession_id``. Each chart is a named series; the hub
      aggregates whatever names arrive and the UI renders them, so neither has to know about a
      new one. A trust that omits a chart is simply skipped for it, which is what lets one roster
      mix imaging and tabular trusts.

    Below-threshold counts are privacy-suppressed by returning a ``StatisticsResponse``
    with ``record_count=0``, empty ``data`` and ``suppressed=True`` — the count itself is
    suppressed, not just the per-field breakdown. A genuine zero is suppressed identically
    to a small (1..threshold-1) count, so the two are indistinguishable on the wire and the
    response cannot be used to infer that >=1 patient matched (membership disclosure — issue
    #519, security review). The ``suppressed`` flag only tells the hub/UI to render a
    "below-threshold" chip instead of a bare 0; it does not reveal which 0s were genuine.
    Suppression is intentional rather than an HTTPException: the response shape stays uniform
    whatever the reason a count fell short, so the reason cannot be read off it. (Historically a
    raise here also left the per-trust UI stuck on "running"; trust-api now reports handler
    errors to the hub, so that is no longer the constraint.) A cohort whose subjects cannot be
    established is suppressed the same way, which the researcher currently sees as a privacy
    refusal rather than a query-shape problem — FLIP#1219 tracks telling them why.

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

    # The floor is on people, not rows (see count_distinct_subjects). A cohort whose subjects
    # cannot be established is suppressed rather than rejected, for two reasons that still hold:
    # an unestablished count is no safer than a small one, and the response shape must not vary
    # with the cause of a shortfall, or the shape itself tells the caller why. (Not because a
    # raise would strand the UI: trust-api reports handler errors to the hub, so a 400 here would
    # surface as an error state. Giving the researcher that diagnosis is FLIP#1219.) The same
    # applies when the count cannot be taken at all — a failed lookup is suppressed, never a 500,
    # on a route that undertakes to answer.
    try:
        subject_count = count_distinct_subjects(df)
    except Exception:
        logger.exception("Subject count unavailable; suppressing the statistics response")
        subject_count = None
    if subject_count is None:
        logger.warning(
            "Cohort exposes neither person_id nor accession_id, or its subject count could not be "
            "taken; suppressing the statistics response"
        )

    if subject_count is None or subject_count < threshold:
        # Privacy-suppress every below-threshold count, INCLUDING a genuine zero: a true
        # zero and a small (1..threshold-1) count return identically (record_count=0,
        # suppressed=True) so the response can't reveal that >=1 patient matched.
        # Distinguishing them would leak membership/existence (issue #519, security review).
        logger.info(
            f"Query returned {record_count} records covering {subject_count} subject(s) "
            f"(< {threshold}); returning privacy-suppressed 0-count response"
        )
        if record_count == 0:
            # Trust-side only, and deliberately AFTER the response has been decided — this
            # cannot and must not change what goes on the wire (see above). It exists because
            # the honest refusal is, by design, indistinguishable and therefore undiagnosable:
            # a vocabulary-less trust answers every cohort query with 0 rows, and the operator
            # sees only "no cohort records" from the hub. See FLIP#967.
            _warn_if_vocabulary_missing()
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

    if ACCESSION_ID_COLUMN in df.columns:
        logger.info("accession_id column found in the query results; including modality distribution.")
        try:
            modality = get_modality_distribution(df)
        except Exception:
            # The chart is descriptive, the response is not: a failed lookup reports no modalities
            # rather than turning a cohort this route has already agreed to answer into a 500.
            logger.exception("Modality lookup failed; reporting no modalities")
            modality = {"name": "Modality Distribution", "results": []}
        # Small buckets group into "Other" like every other distribution: a modality carried by one
        # study is as identifying as an age bracket carried by one patient.
        modality["results"] = make_other_category(modality["results"], min_count=threshold)

        stats.data += [modality]
    return stats
