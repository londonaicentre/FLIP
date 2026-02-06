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

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from data_access_api.main import app
from data_access_api.routers.schema import StatisticsResponse

client = TestClient(app)

# Sample input request and output
sample_query_input = {
    "encrypted_project_id": "my_project",
    "query_id": "1",
    "query_name": "query_1",
    "query": "SELECT * FROM omop.radiology_occurrence",
    "trust_id": "mock_trust",
}

sample_statistics_response = StatisticsResponse(
    query_id="1",
    trust_id="mock_trust",
    record_count=21,
    created="2023-10-01T12:00:00Z",
    data=[
        {
            "name": "modality",
            "results": [{"value": "CT", "count": 21}],
        },
        {
            "name": "manufacturer",
            "results": [
                {"value": "GE", "count": 11},
                {"value": "Siemens", "count": 10},
            ],
        },
    ],
).model_dump()


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.get_statistics")
def test_receive_cohort_query_success(mock_get_statistics, mock_validate_query, mock_get_settings, mock_get_records):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_statistics.return_value = sample_statistics_response

    # Mock DataFrame
    mock_df = pd.DataFrame({"col1": range(10)})
    mock_get_records.return_value = mock_df

    response = client.post("/cohort", json=sample_query_input)

    assert response.status_code == 200
    assert response.json() == sample_statistics_response
    mock_validate_query.assert_called_once_with(sample_query_input["query"])
    mock_get_records.assert_called_once_with(sample_query_input["query"])
    mock_get_statistics.assert_called_once()


@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query")
def test_receive_cohort_query_invalid_validation(mock_validate_query, mock_get_settings):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_validate_query.side_effect = ValueError("Invalid field in query")

    response = client.post("/cohort", json=sample_query_input)

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid field in query"


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query")
@patch("data_access_api.routers.cohort.get_statistics")
def test_receive_cohort_query_statistics_error(
    mock_get_statistics, mock_validate_query, mock_get_settings, mock_get_records
):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_statistics.side_effect = RuntimeError("Statistics computation failed")

    # Mock DataFrame
    mock_df = pd.DataFrame({"col1": range(10)})
    mock_get_records.return_value = mock_df

    response = client.post("/cohort", json=sample_query_input)

    assert response.status_code == 500
    assert response.json()["detail"] == "Statistics computation failed"


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query")
def test_receive_cohort_query_too_few_records(mock_validate_query, mock_get_settings, mock_get_records):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5

    # Mock DataFrame with fewer records than threshold
    mock_df = pd.DataFrame({"col1": range(3)})
    mock_get_records.return_value = mock_df

    response = client.post("/cohort", json=sample_query_input)

    assert response.status_code == 400
    assert response.json()["detail"] == "Query returned too few records: 3 (minimum required: 5)"


@patch("data_access_api.routers.cohort.get_records")
@patch("data_access_api.routers.cohort.get_settings")
@patch("data_access_api.routers.cohort.validate_query")
def test_receive_cohort_query_execution_error(mock_validate_query, mock_get_settings, mock_get_records):
    mock_get_settings.return_value.COHORT_QUERY_THRESHOLD = 5
    mock_get_records.side_effect = Exception("Database connection failed")

    # We expect the exception to propagate and be handled by FastAPI's default exception handler or bubble up
    # Based on the code, it re-raises the exception, so we expect a 500 or the exception itself depending on middleware
    # Since we are using TestClient, unhandled exceptions in the app might raise directly or return 500
    # The code catches Exception and logs it, then re-raises it.
    # FastAPI TestClient will catch the re-raised exception if not handled by an exception handler.
    # However, let's see how the app is configured. Assuming standard FastAPI behavior for unhandled exceptions.

    with pytest.raises(Exception, match="Database connection failed"):
        client.post("/cohort", json=sample_query_input)


# Sample request input
sample_dataframe_query = {
    "encrypted_project_id": "encrypted-id",
    "query": "SELECT age, gender FROM dummy_table",
}

# Expected output
sample_df_dict = {
    "age": [25, 30, 40],
    "gender": ["M", "F", "M"],
}


@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_dataframe_success(mock_get_records, mock_decrypt):
    mock_decrypt.return_value = "decrypted-id"
    mock_df = MagicMock()
    mock_df.to_dict.return_value = sample_df_dict
    mock_get_records.return_value = mock_df

    response = client.post("/cohort/dataframe", json=sample_dataframe_query)

    assert response.status_code == 200
    assert response.json() == sample_df_dict
    mock_decrypt.assert_called_once_with("encrypted-id")
    mock_get_records.assert_called_once_with(sample_dataframe_query["query"])


@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.validate_query")
def test_get_dataframe_invalid_query(mock_validate_query, mock_decrypt):
    mock_decrypt.return_value = "decrypted-id"
    mock_validate_query.side_effect = ValueError("Invalid query syntax")

    response = client.post("/cohort/dataframe", json=sample_dataframe_query)

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid query syntax"


@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_dataframe_sqlalchemy_error(mock_get_records, mock_decrypt):
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.side_effect = SQLAlchemyError("SQLAlchemy error")

    response = client.post("/cohort/dataframe", json=sample_dataframe_query)

    # SQLAlchemyError is caught as a general Exception if not explicitly imported and matched
    assert response.status_code == 500
    assert response.json()["detail"] == "SQLAlchemy error"


@patch("data_access_api.routers.cohort.decrypt")
@patch("data_access_api.routers.cohort.get_records")
def test_get_dataframe_generic_error(mock_get_records, mock_decrypt):
    mock_decrypt.return_value = "decrypted-id"
    mock_get_records.side_effect = RuntimeError("Unexpected failure")

    response = client.post("/cohort/dataframe", json=sample_dataframe_query)

    assert response.status_code == 500
    assert response.json()["detail"] == "Unexpected failure"
