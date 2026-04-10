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

import pytest
from fastapi import HTTPException

from flip_api.cohort_services.save_cohort_query import save_cohort_query
from flip_api.domain.schemas.cohort import CohortQueryInput

# Mocking the project ID for the test
project_id = uuid.uuid4()


@pytest.fixture
def mock_auth_token():
    """Mock authentication token."""
    return "auth-token"


@pytest.fixture
def mock_db_session():
    """Mock database session."""

    class MockSession:
        def __init__(self):
            self.add = MagicMock()
            self.commit = MagicMock()
            self.refresh = MagicMock()
            self.exec = MagicMock(return_value=MagicMock())

    return MockSession()


@patch("flip_api.cohort_services.save_cohort_query.has_project_status", return_value=True)
def test_save_cohort_query(mock_has_project_status, mock_db_session, mock_auth_token):
    # Sample input data
    cohort_query_input = CohortQueryInput(name="Test Query", query="SELECT * FROM table", project_id=project_id)

    # Mock the request
    request = MagicMock()
    request.headers.get.return_value = mock_auth_token

    # Call the function
    result = save_cohort_query(request=request, cohort_query=cohort_query_input, db=mock_db_session)

    # Check the object added to the session
    added_query = mock_db_session.add.call_args[0][0]
    assert added_query.name == cohort_query_input.name
    assert added_query.query == cohort_query_input.query
    assert added_query.project_id == cohort_query_input.project_id

    assert mock_db_session.commit.called
    assert result.query == cohort_query_input.query
    assert result.name == cohort_query_input.name


@patch("flip_api.cohort_services.save_cohort_query.has_project_status", return_value=False)
def test_save_cohort_query_invalid_project_status(mock_has_project_status, mock_db_session, mock_auth_token):
    cohort_query_input = CohortQueryInput(name="Invalid Project", query="SELECT 1", project_id=project_id)

    request = MagicMock()
    request.headers.get.return_value = mock_auth_token

    # Use HTTPException and specify the match for the error message
    with pytest.raises(HTTPException, match="staged/approved") as exc_info:
        save_cohort_query(request=request, cohort_query=cohort_query_input, db=mock_db_session)

    assert exc_info.value.status_code == 400


@patch("flip_api.cohort_services.save_cohort_query.has_project_status", return_value=True)
def test_save_cohort_query_query_id_not_created(mock_has_project_status, mock_db_session, mock_auth_token):
    # Mock refresh to simulate `new_query.id` is None
    def refresh_side_effect(obj):
        obj.id = None

    mock_db_session.refresh.side_effect = refresh_side_effect

    cohort_query_input = CohortQueryInput(name="No ID Query", query="SELECT *", project_id=project_id)

    request = MagicMock()
    request.headers.get.return_value = mock_auth_token

    # Expect an HTTPException with a specific message
    with pytest.raises(HTTPException, match="Could not create query") as exc_info:
        save_cohort_query(request=request, cohort_query=cohort_query_input, db=mock_db_session)

    assert exc_info.value.status_code == 400


@patch("flip_api.cohort_services.save_cohort_query.has_project_status")
def test_save_cohort_query_internal_error(mock_has_project_status, mock_db_session, mock_auth_token):
    # Simulate an unexpected error being raised
    mock_has_project_status.side_effect = Exception("Unexpected DB failure")

    cohort_query_input = CohortQueryInput(name="Boom", query="SELECT crash", project_id=project_id)

    request = MagicMock()
    request.headers.get.return_value = mock_auth_token

    # Expect an HTTPException or a general Exception with a message
    with pytest.raises(HTTPException, match="Internal server error") as exc_info:
        save_cohort_query(request=request, cohort_query=cohort_query_input, db=mock_db_session)

    assert exc_info.value.status_code == 500
