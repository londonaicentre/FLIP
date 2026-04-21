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


# ---- Fixtures ----


@pytest.fixture
def trust_id():
    return uuid4()


@pytest.fixture
def task_id():
    return uuid4()


@pytest.fixture
def mock_trust(trust_id):
    """Create a mock Trust object."""
    trust = MagicMock(spec=Trust)
    trust.id = trust_id
    trust.name = TRUST_NAME
    return trust


@pytest.fixture
def mock_pending_tasks(trust_id):
    """Create mock pending TrustTask objects."""
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


@pytest.fixture
def mock_auth():
    """Override auth dependency to return the default trust name (TRUST_NAME)."""
    app.dependency_overrides[authenticate_trust] = lambda: TRUST_NAME
    yield
    del app.dependency_overrides[authenticate_trust]


def _mock_task_owned_by(trust_id, task_id, task_type=TaskType.COHORT_QUERY):
    """Create a mock TrustTask owned by the given trust."""
    mock_task = MagicMock(spec=TrustTask)
    mock_task.id = task_id
    mock_task.trust_id = trust_id
    mock_task.status = TaskStatus.IN_PROGRESS
    mock_task.task_type = task_type
    return mock_task


# ---- GET /tasks/{trust_name}/pending ----


def test_get_pending_tasks_returns_tasks(mock_trust, mock_pending_tasks, mock_auth):
    """Should return pending tasks and mark them as in_progress."""
    mock_db = MagicMock()
    # First exec call: trust lookup by name; second: pending tasks query
    first_result = MagicMock()
    first_result.first.return_value = mock_trust
    second_result = MagicMock()
    second_result.all.return_value = mock_pending_tasks
    mock_db.exec.side_effect = [first_result, second_result]

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get(f"/api/tasks/{TRUST_NAME}/pending")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["task_type"] == "cohort_query"
    assert data[1]["task_type"] == "create_imaging"

    for task in mock_pending_tasks:
        assert task.status == TaskStatus.IN_PROGRESS

    assert mock_db.commit.called

    del app.dependency_overrides[get_session]


def test_get_pending_tasks_empty(mock_trust, mock_auth):
    """Should return empty list when no pending tasks."""
    mock_db = MagicMock()
    first_result = MagicMock()
    first_result.first.return_value = mock_trust
    second_result = MagicMock()
    second_result.all.return_value = []
    mock_db.exec.side_effect = [first_result, second_result]

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get(f"/api/tasks/{TRUST_NAME}/pending")

    assert response.status_code == 200
    assert response.json() == []

    del app.dependency_overrides[get_session]


def test_get_pending_tasks_trust_not_found():
    """Should return 404 when trust name is not found."""
    app.dependency_overrides[authenticate_trust] = lambda: "NonExistentTrust"

    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = None

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get("/api/tasks/NonExistentTrust/pending")

    assert response.status_code == 404

    del app.dependency_overrides[get_session]
    del app.dependency_overrides[authenticate_trust]


def test_get_pending_tasks_requires_auth():
    """Should return 401 when no API key provided."""
    app.dependency_overrides.pop(authenticate_trust, None)

    mock_db = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get(f"/api/tasks/{TRUST_NAME}/pending")

    assert response.status_code == 401

    del app.dependency_overrides[get_session]


def test_get_pending_tasks_cross_trust_rejected():
    """Should return 403 when authenticated as a different trust than the URL trust_name."""
    app.dependency_overrides[authenticate_trust] = lambda: TRUST_NAME

    mock_db = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get("/api/tasks/Trust_2/pending")

    assert response.status_code == 403
    assert "not authorised" in response.json()["detail"]

    del app.dependency_overrides[get_session]
    del app.dependency_overrides[authenticate_trust]


# ---- POST /tasks/{trust_name}/{task_id}/result ----


def test_submit_task_result_success(trust_id, task_id, mock_trust, mock_auth):
    """Should mark task as COMPLETED on success."""
    mock_task = _mock_task_owned_by(trust_id, task_id)

    mock_db = MagicMock()
    # First exec: trust lookup; second: task lookup
    trust_result = MagicMock()
    trust_result.first.return_value = mock_trust
    task_result = MagicMock()
    task_result.first.return_value = mock_task
    mock_db.exec.side_effect = [trust_result, task_result]

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(
        f"/api/tasks/{TRUST_NAME}/{task_id}/result",
        json={"success": True, "result": '{"data": "test"}'},
    )

    assert response.status_code == 200
    assert mock_task.status == TaskStatus.COMPLETED
    assert mock_task.result == '{"data": "test"}'
    assert mock_db.commit.called

    del app.dependency_overrides[get_session]


def test_submit_task_result_failure(trust_id, task_id, mock_trust, mock_auth):
    """Should mark task as FAILED on failure."""
    mock_task = _mock_task_owned_by(trust_id, task_id)

    mock_db = MagicMock()
    trust_result = MagicMock()
    trust_result.first.return_value = mock_trust
    task_result = MagicMock()
    task_result.first.return_value = mock_task
    mock_db.exec.side_effect = [trust_result, task_result]

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(
        f"/api/tasks/{TRUST_NAME}/{task_id}/result",
        json={"success": False, "result": None},
    )

    assert response.status_code == 200
    assert mock_task.status == TaskStatus.FAILED
    assert mock_db.commit.called

    del app.dependency_overrides[get_session]


def test_submit_task_result_not_found(mock_trust, mock_auth):
    """Should return 404 for unknown task."""
    mock_db = MagicMock()
    trust_result = MagicMock()
    trust_result.first.return_value = mock_trust
    task_result = MagicMock()
    task_result.first.return_value = None
    mock_db.exec.side_effect = [trust_result, task_result]

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(
        f"/api/tasks/{TRUST_NAME}/{uuid4()}/result",
        json={"success": True},
    )

    assert response.status_code == 404

    del app.dependency_overrides[get_session]


def test_submit_task_result_conflict_when_not_in_progress(trust_id, task_id, mock_trust, mock_auth):
    """Should return 409 when task is not IN_PROGRESS."""
    mock_task = _mock_task_owned_by(trust_id, task_id)
    mock_task.status = TaskStatus.COMPLETED

    mock_db = MagicMock()
    trust_result = MagicMock()
    trust_result.first.return_value = mock_trust
    task_result = MagicMock()
    task_result.first.return_value = mock_task
    mock_db.exec.side_effect = [trust_result, task_result]

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(
        f"/api/tasks/{TRUST_NAME}/{task_id}/result",
        json={"success": True},
    )

    assert response.status_code == 409
    assert "not in progress" in response.json()["detail"]

    del app.dependency_overrides[get_session]


def test_submit_task_result_forbidden_for_wrong_trust(trust_id, task_id, mock_auth):
    """Should return 403 when trust tries to submit result for another trust's task."""
    other_trust_id = uuid4()
    mock_task = _mock_task_owned_by(other_trust_id, task_id)  # Task owned by a different trust

    mock_trust = MagicMock(spec=Trust)
    mock_trust.id = trust_id
    mock_trust.name = TRUST_NAME

    mock_db = MagicMock()
    trust_result = MagicMock()
    trust_result.first.return_value = mock_trust
    task_result = MagicMock()
    task_result.first.return_value = mock_task
    mock_db.exec.side_effect = [trust_result, task_result]

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(
        f"/api/tasks/{TRUST_NAME}/{task_id}/result",
        json={"success": True},
    )

    assert response.status_code == 403
    assert "does not belong" in response.json()["detail"]

    del app.dependency_overrides[get_session]


def test_submit_task_result_trust_not_found():
    """Should return 404 when trust name is not found."""
    app.dependency_overrides[authenticate_trust] = lambda: "NonExistentTrust"

    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = None

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(
        f"/api/tasks/NonExistentTrust/{uuid4()}/result",
        json={"success": True},
    )

    assert response.status_code == 404

    del app.dependency_overrides[get_session]
    del app.dependency_overrides[authenticate_trust]


def test_submit_task_result_cross_trust_rejected():
    """Should return 403 when authenticated as a different trust than the URL trust_name."""
    app.dependency_overrides[authenticate_trust] = lambda: TRUST_NAME

    mock_db = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(
        f"/api/tasks/Trust_2/{uuid4()}/result",
        json={"success": True, "result": '{"data": "test"}'},
    )

    assert response.status_code == 403
    assert "not authorised" in response.json()["detail"]

    del app.dependency_overrides[get_session]
    del app.dependency_overrides[authenticate_trust]


# ---- POST /trust/{trust_name}/heartbeat ----


def test_heartbeat_updates_timestamp(mock_trust, mock_auth):
    """Should update the trust's last_heartbeat."""
    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = mock_trust

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(f"/api/trust/{TRUST_NAME}/heartbeat")

    assert response.status_code == 200
    assert mock_trust.last_heartbeat is not None
    assert mock_db.commit.called

    del app.dependency_overrides[get_session]


def test_heartbeat_trust_not_found():
    """Should return 404 for unknown trust name."""
    app.dependency_overrides[authenticate_trust] = lambda: "NonExistentTrust"

    mock_db = MagicMock()
    mock_db.exec.return_value.first.return_value = None

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post("/api/trust/NonExistentTrust/heartbeat")

    assert response.status_code == 404

    del app.dependency_overrides[get_session]
    del app.dependency_overrides[authenticate_trust]


def test_heartbeat_cross_trust_rejected():
    """Should return 403 when authenticated as a different trust than the URL trust_name."""
    app.dependency_overrides[authenticate_trust] = lambda: TRUST_NAME

    mock_db = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post("/api/trust/Trust_2/heartbeat")

    assert response.status_code == 403
    assert "not authorised" in response.json()["detail"]

    del app.dependency_overrides[get_session]
    del app.dependency_overrides[authenticate_trust]


# ---- Email notification on CREATE_IMAGING result ----


@patch("flip_api.private_services.trust_tasks.handle_imaging_task_completed")
def test_submit_create_imaging_result_triggers_email(mock_send_emails, trust_id, task_id, mock_trust, mock_auth):
    """Should call handle_imaging_task_completed for successful CREATE_IMAGING tasks."""
    mock_task = _mock_task_owned_by(trust_id, task_id, TaskType.CREATE_IMAGING)

    mock_db = MagicMock()
    trust_result = MagicMock()
    trust_result.first.return_value = mock_trust
    task_result = MagicMock()
    task_result.first.return_value = mock_task
    mock_db.exec.side_effect = [trust_result, task_result]

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(
        f"/api/tasks/{TRUST_NAME}/{task_id}/result",
        json={"success": True, "result": '{"ID": "img-1", "name": "Test", "created_users": []}'},
    )

    assert response.status_code == 200
    mock_send_emails.assert_called_once_with(mock_task, mock_db)

    del app.dependency_overrides[get_session]


@patch("flip_api.private_services.trust_tasks.handle_imaging_task_completed")
def test_submit_non_imaging_result_does_not_trigger_email(mock_send_emails, trust_id, task_id, mock_trust, mock_auth):
    """Should NOT call handle_imaging_task_completed for non-CREATE_IMAGING tasks."""
    mock_task = _mock_task_owned_by(trust_id, task_id, TaskType.COHORT_QUERY)

    mock_db = MagicMock()
    trust_result = MagicMock()
    trust_result.first.return_value = mock_trust
    task_result = MagicMock()
    task_result.first.return_value = mock_task
    mock_db.exec.side_effect = [trust_result, task_result]

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(
        f"/api/tasks/{TRUST_NAME}/{task_id}/result",
        json={"success": True, "result": '{"data": "test"}'},
    )

    assert response.status_code == 200
    mock_send_emails.assert_not_called()

    del app.dependency_overrides[get_session]


@patch("flip_api.private_services.trust_tasks.handle_imaging_task_completed")
def test_email_failure_does_not_fail_task_submission(mock_send_emails, trust_id, task_id, mock_trust, mock_auth):
    """Email failure should not prevent task result from being recorded."""
    mock_send_emails.side_effect = Exception("SES unavailable")

    mock_task = _mock_task_owned_by(trust_id, task_id, TaskType.CREATE_IMAGING)

    mock_db = MagicMock()
    trust_result = MagicMock()
    trust_result.first.return_value = mock_trust
    task_result = MagicMock()
    task_result.first.return_value = mock_task
    mock_db.exec.side_effect = [trust_result, task_result]

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.post(
        f"/api/tasks/{TRUST_NAME}/{task_id}/result",
        json={"success": True, "result": '{"ID": "img-1", "name": "Test", "created_users": []}'},
    )

    assert response.status_code == 200
    assert mock_task.status == TaskStatus.COMPLETED
    assert mock_db.commit.called

    del app.dependency_overrides[get_session]
