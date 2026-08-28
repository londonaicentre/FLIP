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

"""Unit tests for GET /projects/{id}/cohort-snapshots (FLIP#857 audit surfacing)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import CohortSnapshotStatus, Trust
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


def _snapshot_row(row_count: int = 24, approved: int | None = 24) -> CohortSnapshotStatus:
    return CohortSnapshotStatus(
        project_id=MOCK_PROJECT_ID,
        trust_id=MOCK_TRUST_ID,
        query_id=MOCK_QUERY_ID,
        row_count=row_count,
        approved_record_count=approved,
        has_accessions=True,
        query_hash="abc123",
        snapshot_at=SNAPSHOT_AT,
    )


def test_returns_per_trust_snapshot_records_with_trust_names(client: TestClient, app_fixture: FastAPI):
    mock_db_session = MagicMock()
    trust = Trust(id=MOCK_TRUST_ID, name="GSTT")
    mock_db_session.exec.return_value.all.return_value = [(_snapshot_row(row_count=24, approved=20), trust)]
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    with patch("flip_api.project_services.get_cohort_snapshots.can_access_project", return_value=True) as mock_access:
        response = client.get(f"/api/projects/{MOCK_PROJECT_ID}/cohort-snapshots")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert len(body) == 1
    # camelCase aliases on the wire; drift between frozen and approved counts is visible.
    assert body[0]["trustName"] == "GSTT"
    assert body[0]["rowCount"] == 24
    assert body[0]["approvedRecordCount"] == 20
    assert body[0]["hasAccessions"] is True
    assert body[0]["queryId"] == str(MOCK_QUERY_ID)
    mock_access.assert_called_once_with(MOCK_USER_ID, MOCK_PROJECT_ID, mock_db_session)
    app_fixture.dependency_overrides.clear()


def test_no_snapshots_yet_returns_empty_list(client: TestClient, app_fixture: FastAPI):
    """A project whose trusts have not reported (pending task / pre-feature) is an empty list, not 404."""
    mock_db_session = MagicMock()
    mock_db_session.exec.return_value.all.return_value = []
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    with patch("flip_api.project_services.get_cohort_snapshots.can_access_project", return_value=True):
        response = client.get(f"/api/projects/{MOCK_PROJECT_ID}/cohort-snapshots")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []
    app_fixture.dependency_overrides.clear()


def test_forbidden_without_project_access(client: TestClient, app_fixture: FastAPI):
    mock_db_session = MagicMock()
    app_fixture.dependency_overrides[get_session] = lambda: mock_db_session
    app_fixture.dependency_overrides[verify_token] = lambda: MOCK_USER_ID

    with patch("flip_api.project_services.get_cohort_snapshots.can_access_project", return_value=False):
        response = client.get(f"/api/projects/{MOCK_PROJECT_ID}/cohort-snapshots")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    mock_db_session.exec.assert_not_called()
    app_fixture.dependency_overrides.clear()
