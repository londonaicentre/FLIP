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

from typing import Any

import sqlglot
import sqlglot.errors
import sqlglot.expressions
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from data_access_api.config import get_settings
from data_access_api.routers.schema import (
    AccessionIdsResponse,
    CohortQueryInput,
    DataframeQuery,
    StatisticsResponse,
)
from data_access_api.services.cohort import get_records, get_statistics, validate_query
from data_access_api.utils.encryption import decrypt
from data_access_api.utils.internal_auth import authenticate_internal_service
from data_access_api.utils.logger import logger

_READ_ONLY_STATEMENT_TYPES = (
    sqlglot.expressions.Select,
    sqlglot.expressions.Union,
    sqlglot.expressions.Intersect,
    sqlglot.expressions.Except,
)


def _parse_and_emit(query: str) -> str:
    """Parse SQL with sqlglot and re-emit it to break the injection taint chain.

    Using sqlglot as a parse-then-emit step ensures the string reaching the
    database engine is generated from a validated AST, not directly from the
    HTTP request body.  The output is semantically equivalent to the input for
    all valid SELECT queries while also normalising trailing semicolons and
    whitespace.

    Only read-only SELECT-shaped statements (SELECT, UNION, INTERSECT, EXCEPT)
    are permitted.  DML (INSERT, UPDATE, DELETE) and DDL (DROP, CREATE, ALTER,
    TRUNCATE) are rejected with HTTP 400.

    Args:
        query: Raw SQL string from the caller.

    Returns:
        Re-emitted SQL string.

    Raises:
        HTTPException: 400 if the query cannot be parsed, is empty, contains
            multiple statements, or is not a read-only SELECT statement.
    """
    try:
        transpiled = sqlglot.transpile(query, read="postgres", write="postgres")
    except sqlglot.errors.SqlglotError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid SQL: {exc}") from exc
    if not transpiled or not transpiled[0].strip():
        raise HTTPException(status_code=400, detail="SQL query is empty or could not be parsed")
    if len(transpiled) > 1:
        raise HTTPException(status_code=400, detail="Multiple SQL statements are not allowed")
    try:
        ast = sqlglot.parse_one(transpiled[0], dialect="postgres")
    except sqlglot.errors.SqlglotError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid SQL: {exc}") from exc
    if not isinstance(ast, _READ_ONLY_STATEMENT_TYPES):
        raise HTTPException(status_code=400, detail="Only SELECT statements are allowed")
    return transpiled[0]


# Create Router
router = APIRouter(prefix="/cohort", tags=["Cohort"], dependencies=[Depends(authenticate_internal_service)])


@router.post("", response_model=StatisticsResponse)
def receive_cohort_query(query_input: CohortQueryInput) -> StatisticsResponse:
    """
    Receives a cohort query and returns the aggregated statistics.

    Args:
        query_input (data_access_api.routers.schema.CohortQueryInput): The input data for the cohort query.

    Returns:
        StatisticsResponse: The aggregated statistics from the query results.

    Raises:
        HTTPException: If there is an error during the execution of the query or if the query returns too few records.
    """
    logger.info("Received cohort query")

    minimum_cohort_size = get_settings().COHORT_QUERY_THRESHOLD
    logger.info(f"Minimum cohort size needed to return statistics: {minimum_cohort_size}")

    validate_query(query_input.query)

    # On the original implementation get_records was invoked within get_statistics. However, to better handle
    # exceptions and log the query execution, we separate the two calls here.
    # Execute the query and get the DataFrame
    # TODO: Move this check to centralhub and use the aggregated results only, here we are using partial results from
    # each trust
    safe_query = _parse_and_emit(query_input.query)

    try:
        logger.info("Executing cohort query")

        df = get_records(safe_query)
        df = df.dropna(axis=1, how="all")  # Ignore entirely empty columns
        # drop duplicate columns
        df = df.loc[:, ~df.columns.duplicated()]

        if len(df) < minimum_cohort_size:
            raise HTTPException(
                status_code=400,
                detail=f"Query returned too few records: {len(df)} (minimum required: {minimum_cohort_size})",
            )
    except Exception as e:
        logger.error(f"Error executing query: {str(e)}")
        raise e

    try:
        results = get_statistics(df, query_input=query_input, threshold=minimum_cohort_size)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    logger.info("Cohort query returned results")
    return results


@router.post("/dataframe")
def get_dataframe(query_input: DataframeQuery) -> dict[str, list[Any]]:
    """
    Retrieves query results in a DataFrame-like structure (column-oriented dictionary).

    TODO Do not return certain columns? e.g. "accession_id", "referring_physician", etc.

    1. Decrypt the central hub project ID.
    2. Send the query using get_dataframe(project_id, query).

    Args:
        query_input (DataframeQuery): The input data for the DataFrame query.

    Returns:
        dict[str, list[Any]]: The query results in a DataFrame-like structure.

    Raises:
        HTTPException: If there is an error during the execution of the query or if the query returns too few records.
    """
    project_id = decrypt(query_input.encrypted_project_id)

    logger.info(f"Received DataFrame query for project {project_id}")

    validate_query(query_input.query)

    safe_query = _parse_and_emit(query_input.query)

    try:
        df = get_records(safe_query)
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return df.to_dict(orient="list")


@router.post("/accession-ids", response_model=AccessionIdsResponse)
def get_accession_ids(query_input: DataframeQuery) -> AccessionIdsResponse:
    """
    Returns only the ``accession_id`` column of the cohort, projected server-side.

    The caller's query is wrapped as ``SELECT accession_id FROM (<query>) sub`` so
    no other columns ever cross the trust boundary. This is the minimal-disclosure
    endpoint used by imaging-api to fetch the accession numbers it needs to import
    studies from PACS — it does not expose row-level patient attributes.

    Args:
        query_input (DataframeQuery): The cohort query.

    Returns:
        AccessionIdsResponse: The accession IDs returned by the cohort query.

    Raises:
        HTTPException: If the query is invalid, does not select an ``accession_id``
            column, or fails during execution.
    """
    project_id = decrypt(query_input.encrypted_project_id)

    logger.info(f"Received accession-ids query for project {project_id}")

    validate_query(query_input.query)

    # Parse and re-emit the caller's SQL via sqlglot to break any injection
    # taint chain.  sqlglot also strips trailing semicolons so the inner query
    # composes cleanly inside the outer SELECT subquery.
    safe_inner = _parse_and_emit(query_input.query)
    wrapped_query = f"SELECT accession_id FROM ({safe_inner}) AS cohort_subquery"

    try:
        df = get_records(wrapped_query)
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if "accession_id" not in df.columns:
        raise HTTPException(
            status_code=400,
            detail="Cohort query did not return an 'accession_id' column.",
        )

    accession_ids = [str(value) for value in df["accession_id"].tolist()]
    logger.info(f"accession-ids query for project {project_id} returned {len(accession_ids)} ids")
    return AccessionIdsResponse(accession_ids=accession_ids)
