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


def _persisted_row(sql: str = "SELECT * FROM patients", name: str = "Test Query") -> MagicMock:
    """Stand-in for the Queries row submit now re-reads.

    Submit dispatches the *persisted* SQL rather than the request body, so tests must supply a row
    whose ``query``/``name``/``id`` are the values expected to reach the trusts.
    """
    row = MagicMock()
    row.id = query_id
    row.name = name
    row.query = sql
    return row


def _db(row: MagicMock | None = None, trusts: list | None = None) -> MagicMock:
    """MagicMock session wired for submit's two exec() calls, in order.

    1. the project-scoped Queries lookup (``.first()``)
    2. the Trust listing (``.all()``)

    The order matters: the Queries lookup moved ahead of the trust listing so an unauthorised
    query_id fails before any work is done.
    """
    db = MagicMock()
    db.exec.side_effect = [
        MagicMock(first=MagicMock(return_value=row if row is not None else _persisted_row())),
        MagicMock(all=MagicMock(return_value=list(trusts or []))),
    ]
    return db


@pytest.fixture(autouse=True)
def mock_unstaged():
    """Default every test's project to UNSTAGED — submit now enforces the same gate save does.

    Autouse: only the staged-rejection test below cares about the handle, and without the
    default every other test in this module would 400 on the gate instead of the behaviour it
    is actually asserting.
    """
    with patch("flip_api.cohort_services.submit_cohort_query.has_project_status", return_value=True) as mock:
        yield mock


def test_submit_cohort_query_queues_task(
    mock_request, sample_query, mock_encrypt, mock_can_modify
):
    """Submitting a cohort query should create a TrustTask for each trust."""
    mock_trust = MagicMock(id="trust_1", name="Trust A", endpoint="http://trust-a.com")
    mock_trust.name = "Trust A"
    mock_db = _db(trusts=[mock_trust])

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


def test_submit_cohort_query_with_multiple_trusts(
    mock_request, sample_query, mock_encrypt, mock_can_modify
):
    """Should create one task per trust."""
    mock_trust_a = MagicMock(id="trust_1", name="Trust A", endpoint="http://trust-a.com")
    mock_trust_a.name = "Trust A"
    mock_trust_b = MagicMock(id="trust_2", name="Trust B", endpoint="http://trust-b.com")
    mock_trust_b.name = "Trust B"
    mock_db = _db(trusts=[mock_trust_a, mock_trust_b])

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

    mock_query_row = _persisted_row()
    mock_db = _db(row=mock_query_row, trusts=[mock_trust_a, mock_trust_b])

    submit_cohort_query(mock_request, sample_query, mock_db, user_id)

    assert mock_query_row.queried_trust_ids == [trust_a, trust_b]
    assert mock_db.commit.called


def test_submit_cohort_query_404s_when_query_row_missing(
    mock_request, sample_query, mock_encrypt, mock_can_modify
):
    """A query_id that resolves to no row of this project is refused before any work.

    Previously this warned and carried on, which was tenable when the row only supplied
    queried_trust_ids. It is not tenable now the row supplies the SQL that gets dispatched.
    """
    mock_db = _db(row=None)

    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_request, sample_query, mock_db, user_id)

    assert exc_info.value.status_code == 404
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_called()


def test_submit_cohort_query_scopes_the_query_lookup_to_the_project(
    mock_request, sample_query, mock_encrypt, mock_can_modify
):
    """The Queries lookup must filter on project_id, not just id.

    Asserting on the statement is white-box, but a mock session cannot otherwise distinguish a
    scoped lookup from an unscoped one — and the scoping *is* the security property.

    It must be the WHERE clause specifically: ``select(Queries)`` puts every column in the SELECT
    list, so the string "queries.project_id" appears in the compiled statement whether or not the
    lookup is scoped. Asserting on the full statement passes against the unscoped version.
    """
    mock_db = _db(trusts=[])

    with pytest.raises(HTTPException):
        submit_cohort_query(mock_request, sample_query, mock_db, user_id)

    where = str(mock_db.exec.call_args_list[0][0][0].whereclause)
    assert "queries.project_id" in where


def test_submit_cohort_query_rejected_once_the_project_is_staged(
    mock_request, sample_query, mock_encrypt, mock_can_modify, mock_unstaged
):
    """save_cohort_query freezes the cohort at staging; submit must not re-dispatch past it."""
    mock_unstaged.return_value = False
    mock_db = _db(trusts=[])

    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_request, sample_query, mock_db, user_id)

    assert exc_info.value.status_code == 400
    assert "staged/approved" in exc_info.value.detail
    mock_db.add.assert_not_called()


def test_submit_cohort_query_dispatches_the_persisted_query_not_the_body(
    mock_request, sample_query, mock_encrypt, mock_can_modify
):
    """The SQL sent to the trusts is the SQL of record, even when the body disagrees.

    Queries.query is what the UI renders and what approval is granted against, so allowing the
    body to differ means the audit record and what actually ran against patient data can diverge.
    """
    persisted_sql = "SELECT person_id FROM omop.person"
    mock_trust = MagicMock(id=uuid.uuid4())
    mock_trust.name = "Trust A"
    mock_db = _db(row=_persisted_row(sql=persisted_sql), trusts=[mock_trust])

    # sample_query carries "SELECT * FROM patients" — deliberately different.
    submit_cohort_query(mock_request, sample_query, mock_db, user_id)

    added_task = mock_db.add.call_args[0][0]
    assert persisted_sql in added_task.payload
    assert sample_query.query not in added_task.payload


def _query(sql: str) -> SubmitCohortQuery:
    """Build a SubmitCohortQuery carrying ``sql``, for the pre-check tests below."""
    return SubmitCohortQuery(
        name="Test Query",
        query=sql,
        project_id=project_id,
        query_id=query_id,
        authenticationToken="Bearer test-token",
    )


@pytest.mark.parametrize(
    ("sql", "expected_detail"),
    [
        pytest.param("DROP TABLE patients;", "select", id="non-select"),
        pytest.param("$$$$ not sql at all !!!", None, id="unparseable"),
        pytest.param("SELECT 1; DROP TABLE patients", None, id="stacked-statements"),
        pytest.param(f"SELECT {'a' * (MAX_QUERY_LENGTH + 1)} FROM omop.person", "length", id="oversized"),
    ],
)
@patch("flip_api.cohort_services.submit_cohort_query.can_modify_project", return_value=True)
def test_submit_cohort_query_rejects_invalid_sql(mock_can_modify, mock_auth_request, sql, expected_detail):
    """The hub pre-check rejects the *persisted* SQL before anything is dispatched to the trusts.

    ``unparseable`` is the case the previous sqlparse-based check let through: ``sqlparse.parse``
    is a non-validating tokenizer and returns a truthy result for arbitrary text. ``oversized`` is
    rejected before the parser allocates an AST.
    """
    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_auth_request, _query(sql), _db(row=_persisted_row(sql)), user_id)

    assert exc_info.value.status_code == 400
    if expected_detail:
        assert expected_detail in str(exc_info.value.detail).lower()


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
        submit_cohort_query(mock_request, sample_query, _db(), user_id)

    assert exc_info.value.status_code == 400
    assert "Invalid SQL" in str(exc_info.value.detail)


@patch("flip_api.cohort_services.submit_cohort_query.can_modify_project", return_value=True)
def test_submit_cohort_query_no_trusts(mock_can_modify, mock_request, sample_query):
    """No trusts in the database should return 404."""
    mock_db = _db(trusts=[])

    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_request, sample_query, mock_db, user_id)

    assert exc_info.value.status_code == 404
    assert "No trusts found" in str(exc_info.value.detail)


def test_submit_cohort_query_task_payload_contains_query(
    mock_request, sample_query, mock_encrypt, mock_can_modify
):
    """The task payload should contain the query details."""
    mock_trust = MagicMock(id="trust_1", name="Trust A", endpoint="http://trust-a.com")
    mock_trust.name = "Trust A"
    mock_db = _db(trusts=[mock_trust])

    submit_cohort_query(mock_request, sample_query, mock_db, user_id)

    added_task = mock_db.add.call_args[0][0]
    assert isinstance(added_task, TrustTask)
    # Payload should be a JSON string containing the query
    assert "SELECT * FROM patients" in added_task.payload
    assert "encrypted_project_id" in added_task.payload
