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

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

# Assuming the models are imported like this
from flip_api.cohort_services.get_cohort_query_results import get_cohort_query_results
from flip_api.domain.schemas.status import TaskStatus

# Constants for testing
TEST_QUERY_ID = "query-123"
TEST_USER_ID = "user-abc"

# Mock Data Structure returned from the database exec call
MOCK_STATS = {
    "record_count": 100,
    "trusts_results": [
        {
            "name": "Result A",  # Correct field name
            "results": [  # Correct field name
                {
                    "trust_name": "Trust A",  # Correct field name
                    "trust_id": "trust-1",
                    "data": [  # Correct field name
                        {"value": "some value", "count": 50},
                        {"value": "another value", "count": 50},
                    ],
                }
            ],
        },
        {
            "name": "Result B",  # Correct field name
            "results": [  # Correct field name
                {
                    "trust_name": "Trust B",  # Correct field name
                    "trust_id": "trust-2",
                    "data": [  # Correct field name
                        {"value": "yet another value", "count": 30},
                        {"value": "more data", "count": 70},
                    ],
                }
            ],
        },
    ],
}


def _make_task(status: TaskStatus, result: str | None = None) -> SimpleNamespace:
    """Lightweight stand-in for a TrustTask row, since the endpoint only reads ``status``/``result``."""
    return SimpleNamespace(status=status, result=result)


def _configure_db_mock(
    mock_db: MagicMock,
    *,
    query_exists: bool,
    stats_json: str | None,
    trust_task_rows: list[tuple[SimpleNamespace, str]] | None = None,
) -> None:
    """Wire up the three sequential db.exec() calls made by ``get_cohort_query_results``.

    The endpoint executes three queries in this order:
        1. ``Queries`` lookup → ``.first()`` returns the query id (or ``None``).
        2. ``QueryStats`` lookup → ``.first()`` returns the JSON stats (or ``None``).
        3. ``TrustTask`` + ``Trust`` join → ``.all()`` returns ``(task, trust_name)`` tuples used
           to compute pending state and per-trust failure messages.
    """
    exec_results = [
        MagicMock(first=MagicMock(return_value=TEST_QUERY_ID if query_exists else None)),
        MagicMock(first=MagicMock(return_value=stats_json)),
        MagicMock(all=MagicMock(return_value=trust_task_rows or [])),
    ]
    mock_db.exec.side_effect = exec_results


@patch("flip_api.cohort_services.get_cohort_query_results.can_access_cohort_query", return_value=True)
def test_get_cohort_results_success(mock_access):
    mock_db = MagicMock()
    _configure_db_mock(mock_db, query_exists=True, stats_json=json.dumps(MOCK_STATS))

    result = get_cohort_query_results(query_id=TEST_QUERY_ID, db=mock_db, user_id=TEST_USER_ID)

    # 200 path returns the parsed model directly (FastAPI wraps it in a 200 response)
    assert result.record_count == 100
    assert len(result.trusts_results) == 2
    assert result.failures == []

    # Check trust 1
    trust_a = result.trusts_results[0]
    assert trust_a.name == "Result A"  # Adjusted for name
    assert len(trust_a.results) == 1
    assert trust_a.results[0].trust_name == "Trust A"
    assert trust_a.results[0].trust_id == "trust-1"
    assert len(trust_a.results[0].data) == 2
    assert trust_a.results[0].data[0].value == "some value"
    assert trust_a.results[0].data[0].count == 50

    # Check trust 2
    trust_b = result.trusts_results[1]
    assert trust_b.name == "Result B"  # Adjusted for name
    assert len(trust_b.results) == 1
    assert trust_b.results[0].trust_name == "Trust B"
    assert trust_b.results[0].trust_id == "trust-2"
    assert len(trust_b.results[0].data) == 2
    assert trust_b.results[0].data[0].value == "yet another value"
    assert trust_b.results[0].data[0].count == 30


@patch("flip_api.cohort_services.get_cohort_query_results.can_access_cohort_query", return_value=True)
def test_get_cohort_results_pending_returns_202(mock_access):
    """Query exists, no QueryStats yet, and at least one trust task is still in flight."""
    from fastapi.responses import JSONResponse

    mock_db = MagicMock()
    _configure_db_mock(
        mock_db,
        query_exists=True,
        stats_json=None,
        trust_task_rows=[
            (_make_task(TaskStatus.PENDING), "Trust A"),
            (_make_task(TaskStatus.IN_PROGRESS), "Trust B"),
        ],
    )

    response = get_cohort_query_results(query_id=TEST_QUERY_ID, db=mock_db, user_id=TEST_USER_ID)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 202
    body = json.loads(response.body)
    assert body["status"] == "pending"


@patch("flip_api.cohort_services.get_cohort_query_results.can_access_cohort_query", return_value=True)
def test_get_cohort_results_all_trusts_failed_returns_200_with_failures(mock_access):
    """Every trust task is terminal (FAILED) and no QueryStats exists — surface failures so the
    UI can stop spinning and tell the user what went wrong."""
    mock_db = MagicMock()
    _configure_db_mock(
        mock_db,
        query_exists=True,
        stats_json=None,
        trust_task_rows=[
            (
                _make_task(
                    TaskStatus.FAILED,
                    json.dumps({"error": "400: Query returned too few records: 0 (minimum required: 5)"}),
                ),
                "Trust A",
            ),
            (
                _make_task(TaskStatus.FAILED, json.dumps({"error": "Connection refused"})),
                "Trust B",
            ),
        ],
    )

    result = get_cohort_query_results(query_id=TEST_QUERY_ID, db=mock_db, user_id=TEST_USER_ID)

    # The endpoint returns the pydantic model (FastAPI wraps it in a 200) so the UI can render it
    # instead of polling forever.
    assert result.record_count == 0
    assert result.trusts_results == []
    assert len(result.failures) == 2
    failures_by_trust = {f.trust_name: f.message for f in result.failures}
    assert "too few records" in failures_by_trust["Trust A"]
    assert failures_by_trust["Trust B"] == "Connection refused"


@patch("flip_api.cohort_services.get_cohort_query_results.can_access_cohort_query", return_value=True)
def test_get_cohort_results_partial_failure_includes_failures_with_stats(mock_access):
    """One trust succeeded (so QueryStats exists) and another failed; the failure should be
    surfaced alongside the aggregated stats."""
    mock_db = MagicMock()
    _configure_db_mock(
        mock_db,
        query_exists=True,
        stats_json=json.dumps(MOCK_STATS),
        trust_task_rows=[
            (_make_task(TaskStatus.COMPLETED), "Trust A"),
            (
                _make_task(TaskStatus.FAILED, json.dumps({"error": "Query returned too few records"})),
                "Trust B",
            ),
        ],
    )

    result = get_cohort_query_results(query_id=TEST_QUERY_ID, db=mock_db, user_id=TEST_USER_ID)

    assert result.record_count == 100
    assert len(result.trusts_results) == 2
    assert len(result.failures) == 1
    assert result.failures[0].trust_name == "Trust B"
    assert "too few records" in result.failures[0].message


@patch("flip_api.cohort_services.get_cohort_query_results.can_access_cohort_query", return_value=True)
def test_get_cohort_results_failure_with_non_json_result(mock_access):
    """If a trust task's result column isn't JSON, fall back to the raw string."""
    mock_db = MagicMock()
    _configure_db_mock(
        mock_db,
        query_exists=True,
        stats_json=None,
        trust_task_rows=[
            (_make_task(TaskStatus.FAILED, "boom — plain text error"), "Trust A"),
            (_make_task(TaskStatus.FAILED, None), "Trust B"),
        ],
    )

    result = get_cohort_query_results(query_id=TEST_QUERY_ID, db=mock_db, user_id=TEST_USER_ID)

    failures_by_trust = {f.trust_name: f.message for f in result.failures}
    assert failures_by_trust["Trust A"] == "boom — plain text error"
    # ``None`` result falls back to the generic message rather than surfacing ``None`` to the UI.
    assert "failed" in failures_by_trust["Trust B"].lower()


@patch("flip_api.cohort_services.get_cohort_query_results.can_access_cohort_query", return_value=True)
def test_get_cohort_results_unknown_query_returns_404(mock_access):
    """No Queries row at all — truly unknown query_id, return 404."""
    mock_db = MagicMock()
    _configure_db_mock(mock_db, query_exists=False, stats_json=None)

    with pytest.raises(HTTPException) as exc_info:
        get_cohort_query_results(query_id=TEST_QUERY_ID, db=mock_db, user_id=TEST_USER_ID)

    assert exc_info.value.status_code == 404
    assert "Cohort query not found" in str(exc_info.value.detail)


@patch("flip_api.cohort_services.get_cohort_query_results.can_access_cohort_query", return_value=False)
def test_get_cohort_results_forbidden(mock_access):
    mock_db = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        get_cohort_query_results(query_id=TEST_QUERY_ID, db=mock_db, user_id=TEST_USER_ID)

    assert exc_info.value.status_code == 403
    assert "denied access" in str(exc_info.value.detail)


@patch("flip_api.cohort_services.get_cohort_query_results.can_access_cohort_query", return_value=True)
def test_get_cohort_results_internal_error(mock_access):
    mock_db = MagicMock()
    mock_db.exec.side_effect = Exception("Unexpected DB error")

    with pytest.raises(HTTPException) as exc_info:
        get_cohort_query_results(query_id=TEST_QUERY_ID, db=mock_db, user_id=TEST_USER_ID)

    assert exc_info.value.status_code == 500
    assert "Internal server error" in str(exc_info.value.detail)
