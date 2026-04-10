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

import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException, Request

from flip_api.cohort_services.submit_cohort_query import submit_cohort_query
from flip_api.domain.schemas.cohort import SubmitCohortQuery

# Mocking the project ID for the test
project_id = uuid.uuid4()
query_id = uuid.uuid4()


@pytest.fixture
def mock_auth_request():
    request = MagicMock(spec=Request)
    request.headers = {"Authorization": "Bearer test-token"}
    return request


@pytest.fixture
def sample_query():
    return SubmitCohortQuery(
        name="Test Query",
        query="SELECT * FROM patients",
        project_id=project_id,
        query_id=query_id,
        authenticationToken="Bearer test-token",
    )


@pytest.fixture
def mock_encrypt():
    """Mock the encrypt function to return a fixed value."""
    with patch("flip_api.cohort_services.submit_cohort_query.encrypt", return_value="encrypted_project_id"):
        yield


def test_submit_cohort_query_success(mock_request, sample_query, mock_encrypt):
    # Fake trust and DB session
    mock_db = MagicMock()
    mock_trust = MagicMock(id="trust_1", name="Trust A", endpoint="http://trust-a.com")
    mock_trust.name = "Trust A"
    mock_db.exec.return_value.all.return_value = [mock_trust]

    # Mock the HTTP response with a status code of 200 and a text response
    mock_response = MagicMock()
    mock_response.status_code = 200  # The status code we want to simulate
    mock_response.text = "Success"  # The text we want to simulate

    # Patch `httpx.Client` to return the mock response
    with patch.object(httpx.Client, "post", return_value=mock_response):
        response = submit_cohort_query(mock_request, sample_query, mock_db)

    print(response)

    # Assertions
    assert response.query_id == sample_query.query_id
    assert len(response.trust) == 1
    assert response.trust[0].name == "Trust A"  # Assert that the name is correct
    assert response.trust[0].statusCode == 200  # Assert that the status code is 200


def test_submit_cohort_query_forbidden_sql(mock_auth_request):
    # Create a query with forbidden SQL commands
    query = SubmitCohortQuery(
        name="Hack",
        query="DROP TABLE patients;",  # Forbidden command
        project_id=project_id,
        query_id=query_id,
        authenticationToken=mock_auth_request.headers.get("Authorization", ""),
    )

    # Simulate the function and check for an exception
    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_auth_request, query, MagicMock())

    # Assert the exception details
    assert exc_info.value.status_code == 400
    assert "forbidden SQL commands" in str(exc_info.value.detail)


def test_submit_cohort_query_invalid_sql(monkeypatch, mock_request, sample_query):
    # Simulate invalid SQL syntax using monkeypatch
    monkeypatch.setattr(
        "flip_api.cohort_services.submit_cohort_query.validate_query",
        lambda *_: (_ for _ in ()).throw(ValueError("Invalid SQL")),
    )

    # Simulate the function and check for an exception
    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_request, sample_query, MagicMock())

    # Assert the exception details
    assert exc_info.value.status_code == 400
    assert "Invalid SQL" in str(exc_info.value.detail)


def test_submit_cohort_query_no_trusts(mock_request, sample_query):
    # Simulate a DB with no trusts
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = []  # No trusts found

    # Simulate the function and check for an exception
    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_request, sample_query, mock_db)

    # Assert the exception details
    assert exc_info.value.status_code == 404
    assert "No trusts found" in str(exc_info.value.detail)


def test_submit_cohort_query_trust_error(monkeypatch, mock_request, sample_query, mock_encrypt):
    # Simulate a trust with an endpoint that will throw an error
    mock_db = MagicMock()

    # Create a mock trust object with valid attributes
    mock_trust = MagicMock(id="trust_1", name="Trust Error", endpoint="http://trust-error.com")
    mock_trust.name = "Trust Error"

    # Set the DB query result to return this mock trust
    mock_db.exec.return_value.all.return_value = [mock_trust]

    # Simulate a connection error with a custom FakeClient
    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def post(self, *args, **kwargs):
            raise Exception("Connection failed")

    # Monkeypatch `httpx.Client` to use the fake client
    monkeypatch.setattr("httpx.Client", lambda: FakeClient())

    # Call the function and capture the result
    response = submit_cohort_query(mock_request, sample_query, mock_db)

    # Assert that trust is processed with an error status code and message
    assert len(response.trust) == 1
    assert response.trust[0].statusCode == 500
    assert "Connection failed" in response.trust[0].message
