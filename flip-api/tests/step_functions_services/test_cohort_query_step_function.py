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
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from flip_api.domain.schemas.cohort import (
    SubmitCohortQueryOutput,
    TrustDetails,
)
from flip.main import app
from flip.step_functions_services.retrieve_model_step_function import (
    get_session,
    verify_token,
)

client = TestClient(app)


@pytest.fixture
def cohort_query_input():
    return {"project_id": str(uuid4()), "query": "SELECT * FROM patients WHERE age > 30", "name": "Test Cohort"}


@pytest.fixture(autouse=True)
def override_dependencies():
    mock_session = MagicMock()
    user_id = uuid4()

    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: user_id

    yield mock_session, user_id

    app.dependency_overrides = {}


@patch("flip.step_functions_services.cohort_query_step_function.get_project_by_id")
@patch("flip.step_functions_services.cohort_query_step_function.save_cohort_query")
@patch("flip.step_functions_services.cohort_query_step_function.submit_cohort_query")
def test_cohort_query_success(
    mock_submit,
    mock_save,
    mock_get_project,
    cohort_query_input,
):
    # Mock project exists
    mock_get_project.return_value = True

    # Mock save_cohort_query returns an object with a query_id
    query_id = uuid4()
    mock_save.return_value = MagicMock(query_id=query_id)

    # Mock submit_cohort_query returns expected response
    mock_submit.return_value = SubmitCohortQueryOutput(
        trust=[
            TrustDetails(name="Trust A", statusCode=200),
            TrustDetails(name="Trust B", statusCode=200),
        ],
        query_id=query_id,
    )

    response = client.post("/step/cohort", json=cohort_query_input)

    assert response.status_code == 201
    data = response.json()
    assert data["queryId"] == str(query_id)
    assert len(data["trust"]) == 2
    assert all("name" in t and "statusCode" in t for t in data["trust"])

    mock_get_project.assert_called_once()
    mock_save.assert_called_once()
    mock_submit.assert_called_once()


@patch("flip.step_functions_services.cohort_query_step_function.get_project_by_id")
def test_cohort_query_project_not_found(mock_get_project, cohort_query_input):
    mock_get_project.return_value = None

    response = client.post("/step/cohort", json=cohort_query_input)

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]
    mock_get_project.assert_called_once()


@patch("flip.step_functions_services.cohort_query_step_function.get_project_by_id")
@patch("flip.step_functions_services.cohort_query_step_function.save_cohort_query")
def test_cohort_query_unexpected_exception(mock_save, mock_get_project, cohort_query_input):
    mock_get_project.return_value = True
    mock_save.side_effect = Exception("Database error")

    response = client.post("/step/cohort", json=cohort_query_input)

    assert response.status_code == 500
    assert "Failed to process cohort query" in response.json()["detail"]
