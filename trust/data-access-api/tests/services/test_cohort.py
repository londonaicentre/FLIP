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

from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import HTTPException
from psycopg2 import errors as pg_errors
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from data_access_api.routers.schema import CohortQueryInput
from data_access_api.services.cohort import (
    get_age_distribution,
    get_counts,
    get_null_counts,
    get_records,
    get_sex_distribution,
    get_statistics,
    make_other_category,
    validate_query,
    verify_cardinality,
)


@pytest.fixture
def mock_df():
    # This DataFrame mimics what you expect from the real query
    return pd.DataFrame({
        "modality": ["CT"] * 21,
        "manufacturer": ["GE", "Siemens"] * 10 + ["GE"],
        "accession_id": [f"id_{i}" for i in range(21)],
    })


@pytest.fixture
def mock_df_below_threshold():
    # Smaller dataset for threshold test
    return pd.DataFrame({
        "modality": ["CT", "XR"],
        "manufacturer": ["Discovery", "Discovery"],
        "accession_id": ["id_1", "id_2"],
    })


@patch("pandas.read_sql")
def test_get_statistics(mock_read_sql, mock_df):
    """
    Test the get_statistics function.
    """
    mock_read_sql.return_value = mock_df

    query_input = CohortQueryInput(
        encrypted_project_id="my_project",
        query_id="1",
        query_name="query_1",
        query="SELECT * FROM omop.radiology_occurrence",
        trust_id="mock_trust",
    )

    stats = get_statistics(mock_df, query_input, threshold=10)

    # Check the record count
    assert stats.record_count == 21


@patch("pandas.read_sql")
def test_get_statistics_below_threshold(mock_read_sql, mock_df_below_threshold):
    """
    Test the get_statistics function with a DataFrame that has fewer records than the threshold.
    This should return an empty data list in the StatisticsResponse.
    """
    mock_read_sql.return_value = mock_df_below_threshold

    query_input = CohortQueryInput(
        encrypted_project_id="my_project",
        query_id="2",
        query_name="query_2",
        query="SELECT * FROM omop.radiology_occurrence WHERE omop.radiology_occurrence.manufacturer = 'Discovery'",
        trust_id="mock_trust",
    )
    with pytest.raises(HTTPException) as excinfo:
        get_statistics(mock_df_below_threshold, query_input, threshold=10)
    assert excinfo.value.status_code == 400


@patch("data_access_api.services.cohort.COHORT_QUERY_THRESHOLD", 10)
@patch("pandas.read_sql")
def test_get_statistics_fails_global_threshold(mock_read_sql):
    """
    Test get_statistics when record count is above the requested threshold
    but below the global COHORT_QUERY_THRESHOLD.
    """
    # Create a dataframe with 8 records (between 5 and 10)
    mock_df_medium = pd.DataFrame({
        "modality": ["CT"] * 8,
        "manufacturer": ["GE"] * 8,
        "accession_id": [f"id_{i}" for i in range(8)],
    })

    mock_read_sql.return_value = mock_df_medium

    query_input = CohortQueryInput(
        encrypted_project_id="my_project",
        query_id="3",
        query_name="query_3",
        query="SELECT * FROM omop.radiology_occurrence",
        trust_id="mock_trust",
    )

    # Pass a low threshold (5) so the first check (record_count < threshold) passes (8 < 5 is False)
    # But the global check (len(df) < COHORT_QUERY_THRESHOLD) should fail (8 < 10 is True)
    with pytest.raises(HTTPException) as excinfo:
        get_statistics(mock_df_medium, query_input, threshold=5)

    assert excinfo.value.status_code == 400
    assert "Query returned insufficient results" in excinfo.value.detail


@patch("pandas.read_sql")
def test_get_records_undefined_table_error(mock_read_sql):
    """
    Test get_records with UndefinedTable error.
    """
    # Create mock postgres error
    mock_pg_error = pg_errors.UndefinedTable()
    mock_pg_error.args = ('relation "missing_table" does not exist',)

    # Create mock DBAPIError with the postgres error as orig
    mock_dbapi_error = DBAPIError("statement", "params", mock_pg_error)
    mock_dbapi_error.orig = mock_pg_error

    mock_read_sql.side_effect = mock_dbapi_error

    query = "SELECT * FROM missing_table"

    with pytest.raises(HTTPException, match="The table 'missing_table' does not exist"):
        get_records(query)


@patch("pandas.read_sql")
def test_get_records_undefined_column_error(mock_read_sql):
    """
    Test get_records with UndefinedColumn error.
    """
    # Create mock postgres error
    mock_pg_error = pg_errors.UndefinedColumn()
    mock_pg_error.args = ('column "missing_column" does not exist',)

    # Create mock DBAPIError with the postgres error as orig
    mock_dbapi_error = DBAPIError("statement", "params", mock_pg_error)
    mock_dbapi_error.orig = mock_pg_error

    mock_read_sql.side_effect = mock_dbapi_error

    query = "SELECT missing_column FROM test_table"

    with pytest.raises(HTTPException, match="The column 'missing_column' does not exist"):
        get_records(query)


@patch("pandas.read_sql")
def test_get_records_other_dbapi_error(mock_read_sql):
    """
    Test get_records with other DBAPIError (not UndefinedTable or UndefinedColumn).
    """
    # Create a generic postgres error
    mock_pg_error = Exception("some database error")

    # Create mock DBAPIError with the postgres error as orig
    mock_dbapi_error = DBAPIError("statement", "params", mock_pg_error)
    mock_dbapi_error.orig = mock_pg_error

    mock_read_sql.side_effect = mock_dbapi_error

    query = "SELECT * FROM test_table"

    with pytest.raises(Exception, match="Database error: some database error"):
        get_records(query)


@patch("pandas.read_sql")
def test_get_records_sqlalchemy_error(mock_read_sql):
    """
    Test get_records with SQLAlchemyError.
    """
    mock_sqlalchemy_error = SQLAlchemyError("SQLAlchemy connection error")
    mock_read_sql.side_effect = mock_sqlalchemy_error

    query = "SELECT * FROM test_table"

    with pytest.raises(Exception, match="SQLAlchemy error: SQLAlchemy connection error"):
        get_records(query)


@patch("pandas.read_sql")
def test_get_records_generic_exception(mock_read_sql):
    """
    Test get_records with generic Exception.
    """
    mock_read_sql.side_effect = Exception("Unexpected error")

    query = "SELECT * FROM test_table"

    with pytest.raises(Exception, match="Unexpected error executing query: Unexpected error"):
        get_records(query)


@patch("data_access_api.services.cohort.extract_missing_identifier")
@patch("pandas.read_sql")
def test_get_records_undefined_table_with_extraction_failure(mock_read_sql, mock_extract):
    """
    Test get_records when table name extraction fails.
    """
    # Mock extraction to return None (extraction failure)
    mock_extract.return_value = None

    # Create mock postgres error
    mock_pg_error = pg_errors.UndefinedTable()
    mock_pg_error.args = ("malformed error message",)

    # Create mock DBAPIError with the postgres error as orig
    mock_dbapi_error = DBAPIError("statement", "params", mock_pg_error)
    mock_dbapi_error.orig = mock_pg_error

    mock_read_sql.side_effect = mock_dbapi_error

    query = "SELECT * FROM missing_table"

    with pytest.raises(HTTPException, match="The table 'None' does not exist"):
        get_records(query)


@patch("data_access_api.services.cohort.extract_missing_identifier")
@patch("pandas.read_sql")
def test_get_records_undefined_column_with_extraction_failure(mock_read_sql, mock_extract):
    """
    Test get_records when column name extraction fails.
    """
    # Mock extraction to return None (extraction failure)
    mock_extract.return_value = None

    # Create mock postgres error
    mock_pg_error = pg_errors.UndefinedColumn()
    mock_pg_error.args = ("malformed error message",)

    # Create mock DBAPIError with the postgres error as orig
    mock_dbapi_error = DBAPIError("statement", "params", mock_pg_error)
    mock_dbapi_error.orig = mock_pg_error

    mock_read_sql.side_effect = mock_dbapi_error

    query = "SELECT missing_column FROM test_table"

    with pytest.raises(HTTPException, match="The column 'None' does not exist"):
        get_records(query)


# Tests for validate_query


def test_validate_query():
    """
    Test the validate_query function.
    """
    assert validate_query("SELECT * FROM test_table") is True
    assert validate_query("INVALID SQL") is True

    # Test restricted schemas
    with pytest.raises(HTTPException, match="Query contains restricted PostgreSQL internal functions or schemas."):
        validate_query("SELECT * FROM pg_catalog.pg_tables")
    with pytest.raises(HTTPException, match="Query contains restricted PostgreSQL internal functions or schemas."):
        validate_query("SELECT * FROM information_schema.tables")

    # Test unsafe operations
    with pytest.raises(HTTPException, match="Query contains unsafe operations like DROP, DELETE, or UPDATE."):
        validate_query("DROP TABLE test_table")
    with pytest.raises(HTTPException, match="Query contains unsafe operations like DROP, DELETE, or UPDATE."):
        validate_query("DELETE FROM test_table")
    with pytest.raises(HTTPException, match="Query contains unsafe operations like DROP, DELETE, or UPDATE."):
        validate_query("UPDATE test_table SET col=1")

    # Test INSERT
    with pytest.raises(HTTPException, match="Query contains unsafe operation like INSERT."):
        validate_query("INSERT INTO test_table VALUES (1)")

    # Test CREATE
    with pytest.raises(HTTPException, match="Query contains unsafe operation like CREATE."):
        validate_query("CREATE TABLE test_table (id int)")

    # Test ALTER
    with pytest.raises(HTTPException, match="Query contains unsafe operation like ALTER."):
        validate_query("ALTER TABLE test_table ADD COLUMN col int")

    assert validate_query("") is True


# Tests for get_counts


def test_get_counts_with_data():
    """
    Test get_counts with a DataFrame containing various data types and null values.
    """
    df = pd.DataFrame({
        "column_a": [1, 2, None, 4, 5],
        "column_b": ["x", "y", "z", None, "w"],
        "column_c": [1.1, 2.2, 3.3, 4.4, 5.5],
    })

    result = get_counts(df)

    expected = {
        "name": "Counts",
        "results": [
            {"value": "column\na", "count": 4},  # 4 non-null values
            {"value": "column\nb", "count": 4},  # 4 non-null values
            {"value": "column\nc", "count": 5},  # 5 non-null values
        ],
    }

    assert result == expected


def test_get_counts_empty_dataframe():
    """
    Test get_counts with an empty DataFrame.
    """
    df = pd.DataFrame()

    result = get_counts(df)

    expected = {
        "name": "Counts",
        "results": [],
    }

    assert result == expected


def test_get_counts_all_null_column():
    """
    Test get_counts with a column containing all null values.
    """
    df = pd.DataFrame({
        "all_null": [None, None, None],
        "some_data": [1, 2, 3],
    })

    result = get_counts(df)

    expected = {
        "name": "Counts",
        "results": [
            {"value": "all\nnull", "count": 0},  # 0 non-null values
            {"value": "some\ndata", "count": 3},  # 3 non-null values
        ],
    }

    assert result == expected


# Tests for get_null_counts


def test_get_null_counts_with_data():
    """
    Test get_null_counts with a DataFrame containing various data types and null values.
    """
    df = pd.DataFrame({
        "column_a": [1, 2, None, 4, 5],
        "column_b": ["x", "y", "z", None, "w"],
        "column_c": [1.1, 2.2, 3.3, 4.4, 5.5],
    })

    result = get_null_counts(df)

    expected = {
        "name": "Nulls",
        "results": [
            {"value": "column\na", "count": 1},  # 1 null value
            {"value": "column\nb", "count": 1},  # 1 null value
            {"value": "column\nc", "count": 0},  # 0 null values
        ],
    }

    assert result == expected


def test_get_null_counts_empty_dataframe():
    """
    Test get_null_counts with an empty DataFrame.
    """
    df = pd.DataFrame()

    result = get_null_counts(df)

    expected = {
        "name": "Nulls",
        "results": [],
    }

    assert result == expected


def test_get_null_counts_all_null_column():
    """
    Test get_null_counts with a column containing all null values.
    """
    df = pd.DataFrame({
        "all_null": [None, None, None],
        "some_data": [1, 2, 3],
    })

    result = get_null_counts(df)

    expected = {
        "name": "Nulls",
        "results": [
            {"value": "all\nnull", "count": 3},  # 3 null values
            {"value": "some\ndata", "count": 0},  # 0 null values
        ],
    }

    assert result == expected


def test_get_null_counts_no_nulls():
    """
    Test get_null_counts with a DataFrame containing no null values.
    """
    df = pd.DataFrame({
        "column_a": [1, 2, 3, 4, 5],
        "column_b": ["x", "y", "z", "w", "v"],
        "column_c": [1.1, 2.2, 3.3, 4.4, 5.5],
    })

    result = get_null_counts(df)

    expected = {
        "name": "Nulls",
        "results": [
            {"value": "column\na", "count": 0},  # 0 null values
            {"value": "column\nb", "count": 0},  # 0 null values
            {"value": "column\nc", "count": 0},  # 0 null values
        ],
    }

    assert result == expected


# Tests for get_sex_distribution


def test_get_sex_distribution_no_person_id():
    """
    Test get_sex_distribution when DataFrame doesn't have person_id column.
    """
    df = pd.DataFrame({
        "accession_id": ["id_1", "id_2"],
        "modality": ["CT", "MR"],
    })

    result = get_sex_distribution(df)

    expected = {"name": "Sex Distribution", "results": []}

    assert result == expected


@patch("data_access_api.services.cohort.get_records")
def test_get_sex_distribution_with_person_id(mock_get_records):
    """
    Test get_sex_distribution when DataFrame has person_id column.
    """
    # Mock the input DataFrame with person_id
    df = pd.DataFrame({
        "person_id": [1, 2, 3, 1, 2],  # Some duplicates
        "accession_id": ["id_1", "id_2", "id_3", "id_4", "id_5"],
    })

    # Mock the response from get_records (sex distribution query result)
    mock_sex_data = pd.DataFrame({
        "gender_source_value": ["M", "F"],
        "count": [2, 1],
    })
    mock_get_records.return_value = mock_sex_data

    result = get_sex_distribution(df)

    expected = {
        "name": "Sex Distribution",
        "results": [
            {"value": "M", "count": 2},
            {"value": "F", "count": 1},
        ],
    }

    assert result == expected
    # Verify the SQL query was called with correct person IDs
    mock_get_records.assert_called_once()
    call_args = mock_get_records.call_args[1]["query"]
    assert "1, 2, 3" in call_args  # Unique person IDs
    assert "omop.person" in call_args


# Tests for get_age_distribution


def test_get_age_distribution_no_person_id():
    """
    Test get_age_distribution when DataFrame doesn't have person_id column.
    """
    df = pd.DataFrame({
        "accession_id": ["id_1", "id_2"],
        "modality": ["CT", "MR"],
    })

    result = get_age_distribution(df)

    expected = {"name": "Age Distribution", "results": []}

    assert result == expected


@patch("data_access_api.services.cohort.get_records")
def test_get_age_distribution_with_person_id(mock_get_records):
    """
    Test get_age_distribution when DataFrame has person_id column.
    """
    # Mock the input DataFrame with person_id
    df = pd.DataFrame({
        "person_id": [1, 2, 3],
        "accession_id": ["id_1", "id_2", "id_3"],
    })

    # Mock the response from get_records (age distribution query result)
    mock_age_data = pd.DataFrame({
        "age_group": [20.0, 30.0, 60.0],
        "count": [5, 3, 2],
    })
    mock_get_records.return_value = mock_age_data

    result = get_age_distribution(df)

    expected = {
        "name": "Age Distribution",
        "results": [
            {"value": "20-29", "count": 5},
            {"value": "30-39", "count": 3},
            {"value": "60-69", "count": 2},
        ],
    }

    assert result == expected
    # Verify the SQL query was called
    mock_get_records.assert_called_once()
    call_args = mock_get_records.call_args[1]["query"]
    assert "1, 2, 3" in call_args
    assert "omop.person" in call_args
    assert "birth_datetime" in call_args


# Tests for verify_cardinality


def test_verify_cardinality_sufficient_unique_values():
    """
    Test verify_cardinality with sufficient unique values in all columns.
    """
    df = pd.DataFrame({
        "col1": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],  # 10 unique out of 10 (100%)
        "col2": ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"],  # 10 unique out of 10 (100%)
    })

    result = verify_cardinality(df, threshold=0.05)  # 5% threshold

    assert result is True


def test_verify_cardinality_insufficient_unique_values():
    """
    Test verify_cardinality with insufficient unique values.
    """
    df = pd.DataFrame({
        "col1": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1] * 10,  # 1 unique out of 100 (1%)
        "col2": ["a", "b", "c", "d", "e"] * 20,  # 5 unique out of 100 (5%)
    })

    result = verify_cardinality(df, threshold=0.05)  # 5% threshold

    assert result is False  # col1 fails both absolute (< 5) and relative (< 5%) thresholds


def test_verify_cardinality_mixed_columns():
    """
    Test verify_cardinality with mixed column uniqueness.
    """
    df = pd.DataFrame({
        "good_col": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],  # 10 unique out of 10 (100%)
        "bad_col": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1],  # 1 unique out of 10 (10%)
    })

    result = verify_cardinality(df, threshold=0.20)  # 20% threshold

    assert result is False  # bad_col fails both thresholds (< 5 unique AND < 20%)


def test_verify_cardinality_edge_case_absolute_threshold():
    """
    Test verify_cardinality with exactly 5 unique values (edge case for absolute threshold).
    """
    df = pd.DataFrame({
        "col1": [1, 2, 3, 4, 5] * 20,  # 5 unique out of 100 (5%)
    })

    result = verify_cardinality(df, threshold=0.05)  # 5% threshold

    assert result is True  # Passes because unique_count = 5 (not < 5)


def test_verify_cardinality_only_relative_threshold_fails():
    """
    Test verify_cardinality when only relative threshold fails but absolute passes.
    """
    df = pd.DataFrame({
        "col1": [1, 2, 3, 4, 5, 6] * 20,  # 6 unique out of 120 (5%)
    })

    result = verify_cardinality(df, threshold=0.01)  # 1% threshold

    assert result is True  # Passes because unique_count >= 5 even though percentage < 1%


def test_verify_cardinality_only_absolute_threshold_fails():
    """
    Test verify_cardinality when only absolute threshold fails but relative passes.
    """
    df = pd.DataFrame({
        "col1": [1, 2, 3, 4],  # 4 unique out of 4 (100%)
    })

    result = verify_cardinality(df, threshold=0.05)  # 5% threshold

    assert result is True  # Passes because percentage >= 5% even though unique_count < 5


def test_verify_cardinality_empty_dataframe():
    """
    Test verify_cardinality with empty DataFrame.
    """
    df = pd.DataFrame()

    result = verify_cardinality(df, threshold=0.05)

    assert result is True  # No columns to check


def test_verify_cardinality_single_row():
    """
    Test verify_cardinality with single row DataFrame.
    """
    df = pd.DataFrame({
        "col1": [1],
        "col2": ["a"],
    })

    result = verify_cardinality(df, threshold=2.0)  # 200% threshold (impossible to meet)

    assert result is False  # 1 unique value < 5 absolute threshold AND < 200% relative threshold


# Tests for make_other_category


def test_make_other_category_no_grouping_needed():
    """
    Test make_other_category when all items meet the minimum count threshold.
    """
    results = [
        {"value": "A", "count": 10},
        {"value": "B", "count": 8},
        {"value": "C", "count": 5},
    ]

    result = make_other_category(results, min_count=5)

    expected = [
        {"value": "A", "count": 10},
        {"value": "B", "count": 8},
        {"value": "C", "count": 5},
    ]

    assert result == expected


def test_make_other_category_with_grouping():
    """
    Test make_other_category when some items need to be grouped into 'Other'.
    """
    results = [
        {"value": "A", "count": 10},
        {"value": "B", "count": 8},
        {"value": "C", "count": 3},  # Below threshold
        {"value": "D", "count": 2},  # Below threshold
        {"value": "E", "count": 1},  # Below threshold
    ]

    result = make_other_category(results, min_count=5)

    expected = [
        {"value": "A", "count": 10},
        {"value": "B", "count": 8},
        {"value": "Other", "count": 6},  # 3 + 2 + 1
    ]

    assert result == expected


def test_make_other_category_all_below_threshold():
    """
    Test make_other_category when all items are below the threshold.
    """
    results = [
        {"value": "A", "count": 3},
        {"value": "B", "count": 2},
        {"value": "C", "count": 1},
    ]

    result = make_other_category(results, min_count=5)

    expected = [
        {"value": "Other", "count": 6},  # 3 + 2 + 1
    ]

    assert result == expected


def test_make_other_category_empty_list():
    """
    Test make_other_category with empty results list.
    """
    results = []

    result = make_other_category(results, min_count=5)

    expected = []

    assert result == expected


def test_make_other_category_custom_min_count():
    """
    Test make_other_category with custom min_count parameter.
    """
    results = [
        {"value": "A", "count": 15},
        {"value": "B", "count": 12},
        {"value": "C", "count": 8},  # Below threshold of 10
        {"value": "D", "count": 5},  # Below threshold of 10
    ]

    result = make_other_category(results, min_count=10)

    expected = [
        {"value": "A", "count": 15},
        {"value": "B", "count": 12},
        {"value": "Other", "count": 13},  # 8 + 5
    ]

    assert result == expected


# Additional integration tests for get_statistics with the new helper functions


@patch("pandas.read_sql")
def test_get_statistics_no_person_id_column(mock_read_sql):
    """
    Test get_statistics when DataFrame has no person_id column (should return empty age/sex distributions).
    """
    mock_df = pd.DataFrame({
        "modality": ["CT", "MR", "XR"] * 10,
        "manufacturer": ["GE", "Siemens", "Philips"] * 10,
        "accession_id": [f"id_{i}" for i in range(30)],
    })

    mock_read_sql.return_value = mock_df

    query_input = CohortQueryInput(
        encrypted_project_id="my_project",
        query_id="1",
        query_name="no_person_id_test",
        query="SELECT modality, manufacturer, accession_id FROM omop.radiology_occurrence",
        trust_id="mock_trust",
    )

    result = get_statistics(mock_df, query_input, threshold=10)

    assert result.record_count == 30
    assert len(result.data) == 2

    # Check that counts are present
    counts_data = next((item for item in result.data if item["name"] == "Counts"), None)
    assert counts_data is not None
    assert len(counts_data["results"]) == 3  # 3 columns

    # Check that age and sex distributions are empty
    age_data = next((item for item in result.data if item["name"] == "Age Distribution"), None)
    assert age_data is None

    sex_data = next((item for item in result.data if item["name"] == "Sex Distribution"), None)
    assert sex_data is None


@patch("pandas.read_sql")
def test_get_statistics_with_person_id_column(mock_read_sql):
    """
    Test get_statistics when DataFrame has person_id column.
    Should include age and sex distributions with make_other_category applied.
    """
    # Mock the main query result with person_id
    mock_df = pd.DataFrame({
        "person_id": [1, 2, 3, 4, 5] * 6,  # 30 records with 5 unique person IDs
        "modality": ["CT", "MR", "XR"] * 10,
        "accession_id": [f"id_{i}" for i in range(30)],
    })

    # Mock the age distribution query result
    mock_age_data = pd.DataFrame({
        "age_group": [20.0, 30.0, 40.0],
        "count": [15, 12, 10],  # All >= COHORT_QUERY_THRESHOLD (10)
    })

    # Mock the sex distribution query result
    mock_sex_data = pd.DataFrame({
        "gender_source_value": ["M", "F"],
        "count": [18, 12],  # Both >= COHORT_QUERY_THRESHOLD (10)
    })

    # Configure mocks to return different data based on query
    def read_sql_side_effect(query, *args, **kwargs):
        if "birth_datetime" in query:
            return mock_age_data
        elif "gender_source_value" in query:
            return mock_sex_data
        else:
            # Main query
            return mock_df

    mock_read_sql.side_effect = read_sql_side_effect

    query_input = CohortQueryInput(
        encrypted_project_id="my_project",
        query_id="1",
        query_name="with_person_id_test",
        query="SELECT person_id, modality, accession_id FROM omop.radiology_occurrence",
        trust_id="mock_trust",
    )

    result = get_statistics(mock_df, query_input, threshold=10)

    assert result.record_count == 30
    assert len(result.data) == 4  # Counts, Nulls, Age Distribution, Sex Distribution

    # Check that counts and nulls are present
    counts_data = next((item for item in result.data if item["name"] == "Counts"), None)
    assert counts_data is not None

    nulls_data = next((item for item in result.data if item["name"] == "Nulls"), None)
    assert nulls_data is not None

    # Check that age distribution is present
    age_data = next((item for item in result.data if item["name"] == "Age Distribution"), None)
    assert age_data is not None
    # With COHORT_QUERY_THRESHOLD=10, all counts >= 10 are kept separate
    assert len(age_data["results"]) == 3
    assert {"value": "20-29", "count": 15} in age_data["results"]
    assert {"value": "30-39", "count": 12} in age_data["results"]
    assert {"value": "40-49", "count": 10} in age_data["results"]

    # Check that sex distribution is present
    sex_data = next((item for item in result.data if item["name"] == "Sex Distribution"), None)
    assert sex_data is not None
    # Both M and F have counts >= 10
    assert len(sex_data["results"]) == 2
    assert {"value": "M", "count": 18} in sex_data["results"]
    assert {"value": "F", "count": 12} in sex_data["results"]

    # Verify read_sql was called multiple times (main query + age + sex distributions)
    assert mock_read_sql.call_count >= 3


@patch("pandas.read_sql")
def test_get_statistics_with_person_id_and_low_count_categories(mock_read_sql):
    """
    Test get_statistics when age/sex distributions have low-count categories.
    Should group low-count entries into 'Other' category.
    """
    # Mock the main query result
    mock_df = pd.DataFrame({
        "person_id": list(range(1, 31)),  # 30 unique person IDs
        "modality": ["CT"] * 30,
        "accession_id": [f"id_{i}" for i in range(30)],
    })

    # Mock age distribution with some low-count age groups
    mock_age_data = pd.DataFrame({
        "age_group": [20.0, 30.0, 40.0, 50.0, 60.0],
        "count": [25, 15, 9, 5, 2],  # Last 3 are below threshold of 10
    })

    # Mock sex distribution with low-count category
    mock_sex_data = pd.DataFrame({
        "gender_source_value": ["M", "F", "U"],
        "count": [20, 12, 8],  # 'U' is below threshold of 10
    })

    def read_sql_side_effect(query, *args, **kwargs):
        if "birth_datetime" in query:
            return mock_age_data
        elif "gender_source_value" in query:
            return mock_sex_data
        else:
            return mock_df

    mock_read_sql.side_effect = read_sql_side_effect

    query_input = CohortQueryInput(
        encrypted_project_id="my_project",
        query_id="1",
        query_name="low_count_test",
        query="SELECT person_id, modality, accession_id FROM omop.radiology_occurrence",
        trust_id="mock_trust",
    )

    result = get_statistics(mock_df, query_input, threshold=10)

    # Check age distribution has 'Other' category
    age_data = next((item for item in result.data if item["name"] == "Age Distribution"), None)
    assert age_data is not None
    # With COHORT_QUERY_THRESHOLD=10: 20-29 (25), 30-39 (15) are kept separate;
    # 40-49 (9), 50-59 (5), 60-69 (2) grouped into Other
    assert len(age_data["results"]) == 3
    assert {"value": "20-29", "count": 25} in age_data["results"]
    assert {"value": "30-39", "count": 15} in age_data["results"]
    other_age = next((item for item in age_data["results"] if item["value"] == "Other"), None)
    assert other_age is not None
    assert other_age["count"] == 16  # 9+5+2

    # Check sex distribution has 'Other' category
    sex_data = next((item for item in result.data if item["name"] == "Sex Distribution"), None)
    assert sex_data is not None
    # With COHORT_QUERY_THRESHOLD=10: M (20), F (12) are kept separate; U (8) grouped into Other
    assert len(sex_data["results"]) == 3
    assert {"value": "M", "count": 20} in sex_data["results"]
    assert {"value": "F", "count": 12} in sex_data["results"]
    other_sex = next((item for item in sex_data["results"] if item["value"] == "Other"), None)
    assert other_sex is not None
    assert other_sex["count"] == 8
