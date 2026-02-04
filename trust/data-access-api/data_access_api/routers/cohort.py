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

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from data_access_api.config import get_settings
from data_access_api.routers.schema import CohortQueryInput, DataframeQuery, StatisticsResponse
from data_access_api.services.cohort import get_records, get_statistics, validate_query
from data_access_api.utils.encryption import decrypt
from data_access_api.utils.logger import logger

# Create Router
router = APIRouter(prefix="/cohort", tags=["Cohort"])


@router.post("/", response_model=StatisticsResponse)
def receive_cohort_query(query_input: CohortQueryInput) -> StatisticsResponse:
    """
    Receives a cohort query and returns the aggregated statistics.

    Args:
        query_input (data_access_api.routers.schema.CohortQueryInput): The input data for the cohort query.

    Returns:
        StatisticsResponse: The aggregated statistics from the query results.
    """
    logger.info(f"Received cohort query: {query_input}")

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
        logger.info(f"Executing query: {query_input.query}")

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

    logger.info(f"Response: {results}")
    return results


@router.post("/dataframe")
def get_dataframe(query_input: DataframeQuery) -> Dict[str, List[Any]]:
    """
    Retrieves query results in a DataFrame-like structure (column-oriented dictionary).

    TODO Do not return certain columns? e.g. "accession_id", "referring_physician", etc.

    1. Decrypt the central hub project ID.
    2. Send the query using get_dataframe(project_id, query).

    Args:
        query_input (DataframeQuery): The input data for the DataFrame query.
    Returns:
        Dict[str, List[Any]]: The query results in a DataFrame-like structure.
    """
    project_id = decrypt(query_input.encrypted_project_id)

    logger.info(f"Received DataFrame query for project {project_id} with query: {query_input.query}")

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

    # TODO Potentially store to cache - and at the beginning of the function, check if the query is already in the cache
    # Set cache expiration time e.g. 60 days (if the OMOP database is not updated frequently)
    # This avoids re-running the query if the same one is requested multiple times

    # df.to_csv("data.csv", index=False) # debugging
    return df.to_dict(orient="list")
    return df.to_dict(orient="list")
