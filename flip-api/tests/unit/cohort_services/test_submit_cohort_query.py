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

from flip_api.cohort_services.submit_cohort_query import (
    MAX_QUERY_LENGTH,
    submit_cohort_query,
    validate_query,
)
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


def test_submit_cohort_query_persists_queried_trust_ids(
    mock_request, sample_query, mock_encrypt, mock_can_modify
):
    """The dispatched trust IDs must land on Queries.queried_trust_ids so the
    per-trust UI can render trusts that errored or never responded — otherwise
    the panel only knows about trusts that posted a QueryResult and loses
    visibility of dispatch-time failures."""
    trust_a = uuid.uuid4()
    trust_b = uuid.uuid4()
    mock_trust_a = MagicMock(id=trust_a)
    mock_trust_a.name = "Trust A"
    mock_trust_b = MagicMock(id=trust_b)
    mock_trust_b.name = "Trust B"

    mock_db = MagicMock()
    mock_query_row = MagicMock()
    # Two exec calls: first returns the trust list, second returns the Queries row.
    mock_db.exec.side_effect = [
        MagicMock(all=MagicMock(return_value=[mock_trust_a, mock_trust_b])),
        MagicMock(first=MagicMock(return_value=mock_query_row)),
    ]

    submit_cohort_query(mock_request, sample_query, mock_db, user_id)

    assert mock_query_row.queried_trust_ids == [trust_a, trust_b]
    assert mock_db.commit.called


def test_submit_cohort_query_warns_when_query_row_missing(
    mock_request, sample_query, mock_encrypt, mock_can_modify, caplog
):
    """If the Queries row is missing (save_cohort_query never persisted), the tasks are still
    queued and committed, but a warning is logged so the upstream gap is visible instead of
    silently dropping queried_trust_ids."""
    mock_trust = MagicMock(id=uuid.uuid4())
    mock_trust.name = "Trust A"

    mock_db = MagicMock()
    # First exec → trust list; second exec → Queries lookup returns None (row absent).
    mock_db.exec.side_effect = [
        MagicMock(all=MagicMock(return_value=[mock_trust])),
        MagicMock(first=MagicMock(return_value=None)),
    ]

    response = submit_cohort_query(mock_request, sample_query, mock_db, user_id)

    assert mock_db.add.called
    assert mock_db.commit.called
    assert len(response.trust) == 1
    assert "queried_trust_ids was not persisted" in caplog.text


def _query(sql: str) -> SubmitCohortQuery:
    """Build a SubmitCohortQuery carrying ``sql``, for the pre-check tests below."""
    return SubmitCohortQuery(
        name="Test Query",
        query=sql,
        project_id=project_id,
        query_id=query_id,
        authenticationToken="Bearer test-token",
    )


@patch("flip_api.cohort_services.submit_cohort_query.can_modify_project", return_value=True)
def test_submit_cohort_query_rejects_non_select(mock_can_modify, mock_auth_request):
    """Statements that are not SELECT-shaped are rejected by the hub pre-check."""
    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_auth_request, _query("DROP TABLE patients;"), MagicMock(), user_id)

    assert exc_info.value.status_code == 400
    assert "SELECT" in str(exc_info.value.detail)


@patch("flip_api.cohort_services.submit_cohort_query.can_modify_project", return_value=True)
def test_submit_cohort_query_rejects_unparseable_sql(mock_can_modify, mock_auth_request):
    """Input that is not SQL at all is rejected.

    The previous sqlparse-based check accepted this: ``sqlparse.parse`` is a
    non-validating tokenizer and returns a truthy result for arbitrary text.
    """
    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_auth_request, _query("$$$$ not sql at all !!!"), MagicMock(), user_id)

    assert exc_info.value.status_code == 400


@patch("flip_api.cohort_services.submit_cohort_query.can_modify_project", return_value=True)
def test_submit_cohort_query_rejects_stacked_statements(mock_can_modify, mock_auth_request):
    """Query stacking is rejected — only one statement may be submitted."""
    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(
            mock_auth_request, _query("SELECT 1; DROP TABLE patients"), MagicMock(), user_id
        )

    assert exc_info.value.status_code == 400


@patch("flip_api.cohort_services.submit_cohort_query.can_modify_project", return_value=True)
def test_submit_cohort_query_rejects_oversized_query(mock_can_modify, mock_auth_request):
    """Pathologically large queries are rejected before the parser allocates an AST."""
    oversized = f"SELECT {'a' * (MAX_QUERY_LENGTH + 1)} FROM omop.person"

    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_auth_request, _query(oversized), MagicMock(), user_id)

    assert exc_info.value.status_code == 400
    assert "length" in str(exc_info.value.detail).lower()


def test_validate_query_accepts_substring_function():
    """``SUBSTRING()`` is legitimate SQL and must not be rejected.

    The removed denylist contained the bare token "substring", so every query
    using the standard function was refused — the flexibility cost of a keyword
    denylist. Blind data extraction via ``SUBSTRING`` is defeated trust-side by
    the literal-LIMIT rule, not by banning the function name.
    """
    validate_query("SELECT SUBSTRING(gender_source_value, 1, 1) FROM omop.person")


def test_validate_query_accepts_cte_with_set_operation():
    """Real cohort queries use CTEs and set operations; both are SELECT-shaped."""
    validate_query(
        "WITH a AS (SELECT person_id FROM omop.person) "
        "SELECT person_id FROM a UNION SELECT person_id FROM omop.observation"
    )


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
