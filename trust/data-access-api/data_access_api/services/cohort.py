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
from psycopg2 import errors as pg_errors
from sqlalchemy import bindparam, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.sql.elements import TextClause
from sqlglot import exp
from sqlglot.errors import SqlglotError

from data_access_api.config import get_settings
from data_access_api.db.database import engine
from data_access_api.routers.schema import CohortQueryInput, StatisticsResponse
from data_access_api.services.query_cache import get_cached_result, set_cached_result
from data_access_api.utils.logger import logger
from data_access_api.utils.sql_parsers import extract_missing_identifier

COHORT_QUERY_THRESHOLD = get_settings().COHORT_QUERY_THRESHOLD

# OMOP schema is the only schema callers may reference. Any qualified
# reference to a different schema is rejected by validate_query.
ALLOWED_SCHEMA = "omop"

# Reject pathologically large queries before sqlglot does any work — cheap DoS guard
# at the API layer. The DB role limits blast radius too, but rejecting here is
# defence in depth and stops the parser allocating an arbitrarily large AST.
MAX_QUERY_LENGTH = 10_240  # 10 KiB

# Top-level statement shapes that count as SELECT-like for the cohort API.
_ALLOWED_QUERY_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.Union,
    exp.Intersect,
    exp.Except,
)


def _invalid_query(detail: str) -> HTTPException:
    logger.warning(f"Query validation failed: {detail}")
    return HTTPException(status_code=400, detail=detail)


def validate_query(query: str) -> bool:
    """
    Validates that an inbound SQL query is structurally safe to run against OMOP.

    Database-layer protections already in place
    -------------------------------------------
    The data-access-api connects as ``data_analyst_reader`` (see
    ``flip-omop-db/files/create_readonly_users.sql``), a Postgres role granted
    only ``CONNECT`` + ``USAGE`` on schema ``omop`` + ``SELECT`` on its tables
    and sequences, with ``INSERT``, ``UPDATE``, ``DELETE``, ``TRUNCATE``, and
    ``CREATE`` explicitly REVOKEd. Any DDL or DML is therefore rejected by
    Postgres itself, so this function does NOT keyword-filter for ``DROP`` /
    ``INSERT`` / ``UPDATE`` / etc. — those are already covered at the DB layer.

    What this function enforces
    ---------------------------
    Since the read-only role can still issue arbitrary ``SELECT`` queries:

    1. The query is shorter than ``MAX_QUERY_LENGTH`` (DoS guard).
    2. The query parses as exactly one non-empty statement (defeats query stacking,
       stray semicolons that bypass the count check, and empty inputs).
    3. The top-level statement is SELECT-shaped (rejects ``COPY``, ``EXPLAIN``,
       and any DDL/DML the DB would also reject — fail fast at the API).
    4. Any schema-qualified table reference targets only the ``omop`` schema
       (blocks enumeration of ``information_schema``, ``pg_catalog``,
       ``pg_class`` etc., which Postgres makes readable to role ``public``
       by default).
    5. Every ``LIMIT`` and ``OFFSET`` is a literal integer (defeats the blind
       data-extraction technique that abuses
       ``LIMIT CASE WHEN <predicate> THEN n ELSE m END`` to make the row count
       a function of a single character value, then reads it back via the
       cohort-size error message).

    Args:
        query: The SQL query string from the caller.

    Returns:
        ``True`` when the query is structurally safe.

    Raises:
        HTTPException(400): When any of the rules above is violated.
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

    # Walk the whole AST so subqueries, CTEs, and set-operation arms are checked.
    for table in stmt.find_all(exp.Table):
        schema_node = table.args.get("db")
        if schema_node is None:
            # Unqualified — Postgres resolves via search_path, which is set to
            # the omop schema in the OMOP DB image, so unqualified references
            # can only resolve to omop tables.
            continue
        # exp.Table.args["db"] is always None or an Identifier in sqlglot's
        # schema, so .name is safe here.
        schema_name = schema_node.name.lower()
        if schema_name != ALLOWED_SCHEMA:
            raise _invalid_query(
                f"Schema '{schema_name}' is not accessible. Only the '{ALLOWED_SCHEMA}' schema is allowed."
            )

    for clause_type, label in ((exp.Limit, "LIMIT"), (exp.Offset, "OFFSET")):
        for node in stmt.find_all(clause_type):
            value = node.expression
            if not isinstance(value, exp.Literal) or not value.is_int:
                raise _invalid_query(f"{label} must be a literal integer.")

    return True


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
            raise HTTPException(status_code=500, detail=f"Database error: {error_msg}") from e

    except SQLAlchemyError as e:
        logger.error(f"SQLAlchemy error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"SQLAlchemy error: {str(e)}") from e
    except Exception as e:
        logger.error(f"Unexpected error executing query: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error executing query: {str(e)}") from e


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
            unique_count < COHORT_QUERY_THRESHOLD,  # Absolute threshold
            percentage_unique < threshold,  # Relative threshold
        ]):
            logger.info(f"Column '{col}' has insufficient unique values ({threshold=}, {unique_count=})")
            return False
    return True


def make_other_category(results: list[dict], min_count: int = COHORT_QUERY_THRESHOLD) -> list[dict]:
    """
    Groups entries in the results list with counts less than min_count into an "Other" category.

    Args:
        results (list of dict): List of dictionaries with 'value' and 'count' keys.
        min_count (int): Minimum count threshold to avoid grouping into "Other".

    Returns:
        list of dict: Updated list with low-count entries grouped into "Other".
    """
    other_count = sum(item["count"] for item in results if item["count"] < min_count)
    filtered_results = [item for item in results if item["count"] >= min_count]

    if other_count > 0:
        filtered_results.append({"value": "Other", "count": other_count})

    return filtered_results


def get_statistics(df: pd.DataFrame, query_input: CohortQueryInput, threshold: int) -> StatisticsResponse:
    """
    Returns aggregated statistics from the query results.

    - Counts the number of records.
    - Aggregates the number of occurrences of each unique value per column.

    If the number of records is less than the threshold, an empty response is returned.

    Args:
        df (pd.DataFrame): Query results dataframe.
        query_input (data_access_api.routers.schema.CohortQueryInput): Input object containing the query and metadata.
        threshold (int): Minimum number of records required to return results.

    Returns:
        StatisticsResponse: Contains the aggregated statistics.

    Raises:
        HTTPException: If the request cannot be processed.
    """
    record_count = len(df)

    # Create StatisticsResponse
    stats = StatisticsResponse(
        query_id=query_input.query_id,
        trust_id=query_input.trust_id,
        record_count=record_count,
        created=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
        data=[],
    )

    if record_count < COHORT_QUERY_THRESHOLD:
        logger.info(
            f"Query returned insufficient results ({COHORT_QUERY_THRESHOLD}, {record_count})"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Query returned insufficient results ({COHORT_QUERY_THRESHOLD}, {record_count})",
        )
    stats.data = []
    stats.data += [get_counts(df), get_null_counts(df)]

    if "person_id" in df.columns:
        logger.info("person_id column found in the query results; including age and sex distribution calculations.")
        age = get_age_distribution(df)
        age["results"] = make_other_category(age["results"], min_count=COHORT_QUERY_THRESHOLD)

        sex = get_sex_distribution(df)
        sex["results"] = make_other_category(sex["results"], min_count=COHORT_QUERY_THRESHOLD)

        stats.data += [age, sex]
    return stats
