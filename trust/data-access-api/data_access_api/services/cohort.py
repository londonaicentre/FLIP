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

import pandas as pd
from fastapi import HTTPException
from psycopg2 import errors as pg_errors
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from data_access_api.config import get_settings
from data_access_api.db.database import engine
from data_access_api.routers.schema import CohortQueryInput, StatisticsResponse
from data_access_api.utils.logger import logger
from data_access_api.utils.sql_parsers import extract_missing_identifier

COHORT_QUERY_THRESHOLD = get_settings().COHORT_QUERY_THRESHOLD


def validate_query(query: str) -> bool | HTTPException:
    """
    Validates the SQL query to ensure it is safe to execute.
    This function checks that the query not contain sql injection risks or other unsafe elements.

    Args:
        query (str): The SQL query to validate.

    Returns:
        bool: True if the query is valid, False otherwise.

    Raises:
        HTTPException: If the query is invalid or contains unsafe elements.
    """
    # TODO: Implement more comprehensive validation logic.
    # Check if the user is using PostgreSQL internal functions that are not allowed.
    if "pg_catalog" in query or "information_schema" in query:
        raise HTTPException(
            status_code=400, detail="Query contains restricted PostgreSQL internal functions or schemas."
        )
    if "DROP" in query or "DELETE" in query or "UPDATE" in query:
        raise HTTPException(status_code=400, detail="Query contains unsafe operations like DROP, DELETE, or UPDATE.")
    if "INSERT" in query:
        raise HTTPException(status_code=400, detail="Query contains unsafe operation like INSERT.")
    if "CREATE" in query:
        raise HTTPException(status_code=400, detail="Query contains unsafe operation like CREATE.")
    if "ALTER" in query:
        raise HTTPException(status_code=400, detail="Query contains unsafe operation like ALTER.")
    return True


def get_records(query: str) -> pd.DataFrame:
    """
    Executes a raw SQL query and returns results.

    Args:
        query (str): The SQL query to execute.

    Returns:
        pd.DataFrame: The results of the query as a DataFrame.

    Raises:
        HTTPException: If the query is invalid or if there is an error during execution.
    """
    logger.info(f"Executing SQL query: {query}")

    try:
        # TODO: Trace the query filtering to understand what the final user can see.
        # Executing the query with pandas allows the user to query anything in the database.
        # This is a security risk, but since the input is a query we need to run it.
        # Therefore, we need to validate the query is safe, e.g. only SELECT queries, and does not contain sensitive
        # data.
        # TODO check if we can check column types -- could be used to exclude primary keys, foreign keys, etc.
        df = pd.read_sql(query, engine)
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
    """
    return {
        "name": "Counts",
        "results": [{"value": col.replace("_", "\n"), "count": int(df[col].notnull().sum())} for col in df.columns],
    }


def get_null_counts(df: pd.DataFrame) -> dict:
    """
    Returns counts of null values for each column in the DataFrame.
    """
    return {
        "name": "Nulls",
        "results": [{"value": col.replace("_", "\n"), "count": int(df[col].isnull().sum())} for col in df.columns],
    }


def get_sex_distribution(df: pd.DataFrame) -> dict:
    """
    Returns the distribution of sexes in the DataFrame.
    Assumes the DataFrame has accesion_id, query the table to get the sex distribution.
    """
    if "person_id" not in df.columns:
        return {"name": "Sex Distribution", "results": []}

    sex_counts_database_query = f"""
    SELECT
    p.gender_source_value,
    COUNT(*) AS count
    FROM omop.person p
    WHERE p.person_id IN ({", ".join(df["person_id"].unique().astype(str).tolist())})
    GROUP BY p.gender_source_value
    """
    sex_counts = get_records(
        query=sex_counts_database_query,
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
    """
    if "person_id" not in df.columns:
        return {"name": "Age Distribution", "results": []}

    age_distribution_database_query = f"""
    SELECT
    FLOOR(DATE_PART('year', AGE(CURRENT_DATE, p.birth_datetime)) / 10) * 10 AS age_group,
    COUNT(*) AS count
    FROM omop.person p
    WHERE p.person_id IN ({", ".join(df["person_id"].unique().astype(str).tolist())})
    GROUP BY age_group
    ORDER BY age_group
    """
    age_distribution = get_records(
        query=age_distribution_database_query,
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
            f"Query {query_input.query} returned insufficient results ({COHORT_QUERY_THRESHOLD}, {record_count})"
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
        age["results"] = make_other_category(get_age_distribution(df)["results"], min_count=COHORT_QUERY_THRESHOLD)

        sex = get_sex_distribution(df)
        sex["results"] = make_other_category(get_sex_distribution(df)["results"], min_count=COHORT_QUERY_THRESHOLD)

        stats.data += [age, sex]
    return stats
