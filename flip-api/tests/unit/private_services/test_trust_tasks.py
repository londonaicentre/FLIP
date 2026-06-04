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

"""Trust ↔ hub endpoint tests.

Identity comes from ``Depends(authenticate_trust)``, which returns the resolved
``Trust`` row. Routes are key-only (no ``{trust_name}`` segment).
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from flip_api.auth.access_manager import authenticate_trust
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust, TrustTask
from flip_api.domain.schemas.status import TaskStatus, TaskType
from flip_api.main import app

client = TestClient(app)

TRUST_NAME = "Trust_1"


@pytest.fixture
def trust_id():
    return uuid4()


@pytest.fixture
def task_id():
    return uuid4()


@pytest.fixture
def mock_trust(trust_id):
    """A Trust row the auth dependency would return for a valid key."""
    trust = MagicMock(spec=Trust)
    trust.id = trust_id
    trust.name = TRUST_NAME
    return trust


@pytest.fixture
def mock_auth(mock_trust):
    """Override auth so every request resolves to ``mock_trust``."""
    app.dependency_overrides[authenticate_trust] = lambda: mock_trust
    yield
    app.dependency_overrides.pop(authenticate_trust, None)


@pytest.fixture
def mock_pending_tasks(trust_id):
    """Two PENDING tasks owned by ``trust_id``."""
    return [
        TrustTask(
            id=uuid4(),
            trust_id=trust_id,
            task_type=TaskType.COHORT_QUERY,
            payload='{"query": "SELECT 1"}',
            status=TaskStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        ),
        TrustTask(
            id=uuid4(),
            trust_id=trust_id,
            task_type=TaskType.CREATE_IMAGING,
            payload='{"project_id": "123"}',
            status=TaskStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        ),
    ]


def _mock_task_owned_by(owner_trust_id, task_id, task_type=TaskType.COHORT_QUERY):
    """A mock TrustTask in ``IN_PROGRESS`` owned by ``owner_trust_id``."""
    task = MagicMock(spec=TrustTask)
    task.id = task_id
    task.trust_id = owner_trust_id
    task.status = TaskStatus.IN_PROGRESS
    task.task_type = task_type
    return task


# ---- GET /tasks/pending (canonical key-only route) ----


def test_get_pending_tasks_returns_tasks_with_identity(mock_pending_tasks, mock_auth, mock_trust):
    """The response carries the resolved {trust_id, trust_name} + the tasks list."""
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = mock_pending_tasks
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get("/api/tasks/pending")

    assert response.status_code == 200
    data = response.json()
    assert data["trust_id"] == str(mock_trust.id)
    assert data["trust_name"] == TRUST_NAME
    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["task_type"] == "cohort_query"
    assert data["tasks"][1]["task_type"] == "create_imaging"
    for task in mock_pending_tasks:
        assert task.status == TaskStatus.IN_PROGRESS
    assert mock_db.commit.called

    app.dependency_overrides.pop(get_session, None)


def test_get_pending_tasks_empty(mock_auth, mock_trust):
    """No queued tasks — handler still returns the identity block."""
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = []
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get("/api/tasks/pending")

    assert response.status_code == 200
    data = response.json()
    assert data["trust_id"] == str(mock_trust.id)
    assert data["trust_name"] == TRUST_NAME
    assert data["tasks"] == []

    app.dependency_overrides.pop(get_session, None)


def test_get_pending_tasks_requires_auth():
    """No auth override + no API key header → 401 from the dependency."""
    app.dependency_overrides.pop(authenticate_trust, None)
    app.dependency_overrides[get_session] = lambda: MagicMock()

    response = client.get("/api/tasks/pending")

    assert response.status_code == 401
    app.dependency_overrides.pop(get_session, None)


# ---- POST /tasks/{task_id}/result (canonical key-only route) ----


def test_submit_task_result_success(trust_id, task_id, mock_auth, mock_trust):
    """A successful submission marks the task COMPLETED and includes identity."""
    mock_task = _mock_task_owned_by(trust_id, task_id)
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = mock_task
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(
        f"/api/tasks/{task_id}/result",
        json={"success": True, "result": '{"data": "test"}'},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["trust_id"] == str(mock_trust.id)
    assert data["trust_name"] == TRUST_NAME
    assert mock_task.status == TaskStatus.COMPLETED
    assert mock_task.result == '{"data": "test"}'
    assert mock_db.commit.called

    app.dependency_overrides.pop(get_session, None)


def test_submit_task_result_failure(trust_id, task_id, mock_auth):
    """A failed submission marks the task FAILED."""
    mock_task = _mock_task_owned_by(trust_id, task_id)
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = mock_task
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(f"/api/tasks/{task_id}/result", json={"success": False, "result": None})

    assert response.status_code == 200
    assert mock_task.status == TaskStatus.FAILED
    assert mock_db.commit.called

    app.dependency_overrides.pop(get_session, None)


def test_submit_task_result_not_found(mock_auth):
    """An unknown task id → 404."""
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = None
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(f"/api/tasks/{uuid4()}/result", json={"success": True})

    assert response.status_code == 404
    app.dependency_overrides.pop(get_session, None)


def test_submit_task_result_conflict_when_not_in_progress(trust_id, task_id, mock_auth):
    """A task already terminated → 409."""
    mock_task = _mock_task_owned_by(trust_id, task_id)
    mock_task.status = TaskStatus.COMPLETED
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = mock_task
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(f"/api/tasks/{task_id}/result", json={"success": True})

    assert response.status_code == 409
    assert "not in progress" in response.json()["detail"]
    app.dependency_overrides.pop(get_session, None)


def test_submit_task_result_forbidden_for_other_trusts_task(task_id, mock_auth):
    """A task owned by a different trust → 403; the API key alone is not enough."""
    other_trust_id = uuid4()
    mock_task = _mock_task_owned_by(other_trust_id, task_id)
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = mock_task
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(f"/api/tasks/{task_id}/result", json={"success": True})

    assert response.status_code == 403
    assert "does not belong" in response.json()["detail"]
    app.dependency_overrides.pop(get_session, None)


# ---- POST /trust/heartbeat (canonical key-only route) ----


def test_heartbeat_updates_timestamp_and_returns_identity(mock_auth, mock_trust):
    """Heartbeat stamps last_heartbeat and surfaces the resolved identity."""
    mock_db = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post("/api/trust/heartbeat")

    assert response.status_code == 200
    data = response.json()
    assert data["trust_id"] == str(mock_trust.id)
    assert data["trust_name"] == TRUST_NAME
    assert mock_trust.last_heartbeat is not None
    assert mock_db.commit.called

    app.dependency_overrides.pop(get_session, None)


# ---- Email notification on CREATE_IMAGING result ----


@patch("flip_api.private_services.trust_tasks.handle_imaging_task_completed")
def test_submit_create_imaging_result_triggers_email(mock_send_emails, trust_id, task_id, mock_auth):
    mock_task = _mock_task_owned_by(trust_id, task_id, TaskType.CREATE_IMAGING)
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = mock_task
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(
        f"/api/tasks/{task_id}/result",
        json={"success": True, "result": '{"ID": "img-1", "name": "Test", "created_users": []}'},
    )

    assert response.status_code == 200
    mock_send_emails.assert_called_once_with(mock_task, mock_db)
    app.dependency_overrides.pop(get_session, None)


@patch("flip_api.private_services.trust_tasks.handle_imaging_task_completed")
def test_submit_non_imaging_result_does_not_trigger_email(mock_send_emails, trust_id, task_id, mock_auth):
    mock_task = _mock_task_owned_by(trust_id, task_id, TaskType.COHORT_QUERY)
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = mock_task
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(f"/api/tasks/{task_id}/result", json={"success": True, "result": '{"data": "test"}'})

    assert response.status_code == 200
    mock_send_emails.assert_not_called()
    app.dependency_overrides.pop(get_session, None)


@patch("flip_api.private_services.trust_tasks.handle_imaging_task_completed")
def test_email_failure_does_not_fail_task_submission(mock_send_emails, trust_id, task_id, mock_auth):
    mock_send_emails.side_effect = Exception("SES unavailable")
    mock_task = _mock_task_owned_by(trust_id, task_id, TaskType.CREATE_IMAGING)
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = mock_task
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(
        f"/api/tasks/{task_id}/result",
        json={"success": True, "result": '{"ID": "img-1", "name": "Test", "created_users": []}'},
    )

    assert response.status_code == 200
    assert mock_task.status == TaskStatus.COMPLETED
    assert mock_db.commit.called
    app.dependency_overrides.pop(get_session, None)
