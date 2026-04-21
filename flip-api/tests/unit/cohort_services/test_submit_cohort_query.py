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
from fastapi import HTTPException, Request

from flip_api.cohort_services.submit_cohort_query import submit_cohort_query
from flip_api.db.models.main_models import TrustTask
from flip_api.domain.schemas.cohort import SubmitCohortQuery
from flip_api.domain.schemas.status import TaskType

# Mocking the project ID for the test
project_id = uuid.uuid4()
query_id = uuid.uuid4()
user_id = uuid.uuid4()


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


@pytest.fixture
def mock_can_modify():
    """Mock can_modify_project to return True (user has permission)."""
    with patch("flip_api.cohort_services.submit_cohort_query.can_modify_project", return_value=True):
        yield


def test_submit_cohort_query_queues_task(mock_request, sample_query, mock_encrypt, mock_can_modify):
    """Submitting a cohort query should create a TrustTask for each trust."""
    mock_db = MagicMock()
    mock_trust = MagicMock(id="trust_1", name="Trust A", endpoint="http://trust-a.com")
    mock_trust.name = "Trust A"
    mock_db.exec.return_value.all.return_value = [mock_trust]

    response = submit_cohort_query(mock_request, sample_query, mock_db, user_id)

    # Verify a TrustTask was added to the DB
    assert mock_db.add.called
    added_obj = mock_db.add.call_args[0][0]
    assert isinstance(added_obj, TrustTask)
    assert added_obj.task_type == TaskType.COHORT_QUERY
    assert added_obj.trust_id == "trust_1"

    # Verify commit was called
    assert mock_db.commit.called

    # Verify response
    assert response.query_id == sample_query.query_id
    assert len(response.trust) == 1
    assert response.trust[0].name == "Trust A"
    assert response.trust[0].statusCode == 202
    assert response.trust[0].message == "Task queued"


def test_submit_cohort_query_multiple_trusts(mock_request, sample_query, mock_encrypt, mock_can_modify):
    """Should create one task per trust."""
    mock_db = MagicMock()
    mock_trust_a = MagicMock(id="trust_1", name="Trust A", endpoint="http://trust-a.com")
    mock_trust_a.name = "Trust A"
    mock_trust_b = MagicMock(id="trust_2", name="Trust B", endpoint="http://trust-b.com")
    mock_trust_b.name = "Trust B"
    mock_db.exec.return_value.all.return_value = [mock_trust_a, mock_trust_b]

    response = submit_cohort_query(mock_request, sample_query, mock_db, user_id)

    # Two tasks should be added
    assert mock_db.add.call_count == 2
    assert len(response.trust) == 2
    assert all(t.statusCode == 202 for t in response.trust)


@patch("flip_api.cohort_services.submit_cohort_query.can_modify_project", return_value=True)
def test_submit_cohort_query_forbidden_sql(mock_can_modify, mock_auth_request):
    """Queries with forbidden SQL commands should be rejected."""
    query = SubmitCohortQuery(
        name="Hack",
        query="DROP TABLE patients;",
        project_id=project_id,
        query_id=query_id,
        authenticationToken=mock_auth_request.headers.get("Authorization", ""),
    )

    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_auth_request, query, MagicMock(), user_id)

    assert exc_info.value.status_code == 400
    assert "forbidden SQL commands" in str(exc_info.value.detail)


@patch("flip_api.cohort_services.submit_cohort_query.can_modify_project", return_value=True)
def test_submit_cohort_query_invalid_sql(mock_can_modify, monkeypatch, mock_request, sample_query):
    """Invalid SQL should be rejected."""
    monkeypatch.setattr(
        "flip_api.cohort_services.submit_cohort_query.validate_query",
        lambda *_: (_ for _ in ()).throw(ValueError("Invalid SQL")),
    )

    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_request, sample_query, MagicMock(), user_id)

    assert exc_info.value.status_code == 400
    assert "Invalid SQL" in str(exc_info.value.detail)


@patch("flip_api.cohort_services.submit_cohort_query.can_modify_project", return_value=True)
def test_submit_cohort_query_no_trusts(mock_can_modify, mock_request, sample_query):
    """No trusts in the database should return 404."""
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = []

    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_request, sample_query, mock_db, user_id)

    assert exc_info.value.status_code == 404
    assert "No trusts found" in str(exc_info.value.detail)


def test_submit_cohort_query_task_payload_contains_query(mock_request, sample_query, mock_encrypt, mock_can_modify):
    """The task payload should contain the query details."""
    mock_db = MagicMock()
    mock_trust = MagicMock(id="trust_1", name="Trust A", endpoint="http://trust-a.com")
    mock_trust.name = "Trust A"
    mock_db.exec.return_value.all.return_value = [mock_trust]

    submit_cohort_query(mock_request, sample_query, mock_db, user_id)

    added_task = mock_db.add.call_args[0][0]
    assert isinstance(added_task, TrustTask)
    # Payload should be a JSON string containing the query
    assert "SELECT * FROM patients" in added_task.payload
    assert "encrypted_project_id" in added_task.payload
