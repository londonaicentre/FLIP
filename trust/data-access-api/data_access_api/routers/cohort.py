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

# Create Router
router = APIRouter(prefix="/cohort", tags=["Cohort"], dependencies=[Depends(authenticate_internal_service)])


@router.post("/", response_model=StatisticsResponse)
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

    # Validate the query
    try:
        validate_query(query_input.query)
    except ValueError as e:
        logger.error(f"Invalid query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    # On the original implementation get_records was invoked within get_statistics. However, to better handle
    # exceptions and log the query execution, we separate the two calls here.
    # Execute the query and get the DataFrame
    # TODO: Move this check to centralhub and use the aggregated results only, here we are using partial results from
    # each trust
    try:
        logger.info("Executing cohort query")

        df = get_records(query_input.query)
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

    # Validate the query
    try:
        validate_query(query_input.query)
    except ValueError as e:
        logger.error(f"Invalid query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    try:
        df = get_records(query_input.query)
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

    try:
        validate_query(query_input.query)
    except ValueError as e:
        logger.error(f"Invalid query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    # Strip trailing whitespace and a single optional trailing semicolon so the
    # caller's SQL composes cleanly inside a SELECT subquery.
    inner_query = query_input.query.rstrip().rstrip(";").rstrip()
    wrapped_query = f"SELECT accession_id FROM ({inner_query}) AS cohort_subquery"

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
