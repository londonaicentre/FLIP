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

"""Unit tests for GET /projects/{id}/cohort-snapshots (FLIP#857 per-trust freeze state)."""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import CohortSnapshotStatus, Trust, TrustTask
from flip_api.domain.schemas.status import TaskStatus, TaskType
from flip_api.project_services.get_cohort_snapshots import router as get_cohort_snapshots_router

MOCK_USER_ID = uuid4()
MOCK_PROJECT_ID = uuid4()
MOCK_TRUST_ID = uuid4()
MOCK_QUERY_ID = uuid4()
SNAPSHOT_AT = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def app_fixture() -> FastAPI:
    app = FastAPI()
    app.include_router(get_cohort_snapshots_router, prefix="/api")
    return app


@pytest.fixture
def client(app_fixture: FastAPI) -> TestClient:
    return TestClient(app_fixture)


def _snapshot_row(trust_id=MOCK_TRUST_ID, row_count: int = 24, approved: int | None = 24) -> CohortSnapshotStatus:
    return CohortSnapshotStatus(
        project_id=MOCK_PROJECT_ID,
        trust_id=trust_id,
        query_id=MOCK_QUERY_ID,
        row_count=row_count,
        approved_record_count=approved,
        has_accessions=True,
        query_hash="abc123",
        snapshot_at=SNAPSHOT_AT,
    )


def _task(trust_id, task_status: TaskStatus, result: dict | None = None) -> TrustTask:
    return TrustTask(
        trust_id=trust_id,
        task_type=TaskType.PERSIST_COHORT,
        payload="{}",
        query_id=MOCK_QUERY_ID,
        status=task_status,
        result=json.dumps(result) if result else None,
    )


def _db(tasks, records):
    """exec() answers the PERSIST_COHORT task lookup, then the snapshot-record lookup."""
    db = MagicMock()
    task_result, record_result = MagicMock(), MagicMock()
    task_result.all.return_value = tasks
    record_result.all.return_value = records
    db.exec.side_effect = [task_result, record_result]
    return db


def _get(client, app_fixture, db, trusts):
    app_fixture.dependency_overrides[get_session] = lambda: db
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID
    with (
        patch("flip_api.project_services.get_cohort_snapshots.can_access_project", return_value=True) as access,
        patch("flip_api.project_services.get_cohort_snapshots.get_approved_trusts_for_project", return_value=trusts),
    ):
        response = client.get(f"/api/projects/{MOCK_PROJECT_ID}/cohort-snapshots")
    app_fixture.dependency_overrides.clear()
    return response, access


def test_frozen_trust_carries_its_approval_time_facts(client: TestClient, app_fixture: FastAPI):
    db = _db([_task(MOCK_TRUST_ID, TaskStatus.COMPLETED)], [_snapshot_row(row_count=24, approved=20)])

    response, access = _get(client, app_fixture, db, [Trust(id=MOCK_TRUST_ID, name="GSTT")])

    assert response.status_code == status.HTTP_200_OK
    (entry,) = response.json()
    # camelCase aliases on the wire; drift between frozen and approved counts is visible.
    assert entry["status"] == "frozen"
    assert entry["error"] is None
    assert entry["trustName"] == "GSTT"
    assert entry["rowCount"] == 24
    assert entry["approvedRecordCount"] == 20
    assert entry["hasAccessions"] is True
    assert entry["queryId"] == str(MOCK_QUERY_ID)
    access.assert_called_once_with(MOCK_USER_ID, MOCK_PROJECT_ID, db)


def test_every_approved_trust_is_listed_with_its_state(client: TestClient, app_fixture: FastAPI):
    """Pending and failed trusts are visible, not missing — training at either is refused."""
    frozen, pending, failed, never = (Trust(id=uuid4(), name=name) for name in ("A", "B", "C", "D"))
    db = _db(
        [
            _task(frozen.id, TaskStatus.COMPLETED),
            _task(pending.id, TaskStatus.IN_PROGRESS),
            _task(failed.id, TaskStatus.FAILED, {"error": "psycopg2 at omop-db-1:5432", "status_code": 403}),
        ],
        [_snapshot_row(trust_id=frozen.id)],
    )

    response, _ = _get(client, app_fixture, db, [frozen, pending, failed, never])

    body = response.json()
    assert [(e["trustName"], e["status"]) for e in body] == [
        ("A", "frozen"),
        ("B", "pending"),
        ("C", "failed"),
        ("D", "failed"),
    ]
    assert body[1]["rowCount"] is None
    # Category only: the trust's raw error text never reaches the wire.
    assert "Refused by the trust" in body[2]["error"]
    assert "psycopg2" not in response.text
    assert body[3]["error"] == "No cohort snapshot was requested at this trust"


def test_no_approved_trusts_returns_empty_list(client: TestClient, app_fixture: FastAPI):
    db = MagicMock()

    response, _ = _get(client, app_fixture, db, [])

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def test_forbidden_without_project_access(client: TestClient, app_fixture: FastAPI):
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    with patch("flip_api.project_services.get_cohort_snapshots.can_access_project", return_value=False):
        response = client.get(f"/api/projects/{MOCK_PROJECT_ID}/cohort-snapshots")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    mock_db_session.exec.assert_not_called()
    app_fixture.dependency_overrides.clear()
