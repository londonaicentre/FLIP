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

"""Unit tests for the authenticated trust-health endpoint (GET /trust/health).

Powers the online/offline indicators on the Connection Status page, polled from App.vue
alongside GET /trust. Like that sibling it is authenticated but deliberately NOT admin-gated:
every signed-in role may read it, because the roster is benign platform metadata. What it must
never do again is answer an anonymous caller — it shipped as the only project route with no auth
dependency, so the roster of participating trusts and their liveness was readable by anyone who
could reach the hub URL. ``test_unauthenticated_request_is_rejected`` pins that, and
``tests/unit/auth/test_route_auth_inventory.py`` pins the repo-wide allowlist of routes permitted
to have no auth at all.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust
from flip_api.main import app

client = TestClient(app)

# ---- Fixtures ----


@pytest.fixture
def authenticated():
    """Stand in for a signed-in caller of any role, and always undo it — even when a test fails."""
    app.dependency_overrides[verify_token] = lambda: uuid4()
    yield
    del app.dependency_overrides[verify_token]


@pytest.fixture
def mock_trusts_online():
    """Trusts with recent heartbeats (should be online)."""
    now = datetime.now(timezone.utc)
    return [
        Trust(id=uuid4(), name="Trust A", last_heartbeat=now - timedelta(seconds=5)),
        Trust(id=uuid4(), name="Trust B", last_heartbeat=now - timedelta(seconds=10)),
    ]


@pytest.fixture
def mock_trusts_stale():
    """Trusts with stale heartbeats (should be offline)."""
    old_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    return [
        Trust(id=uuid4(), name="Trust A", last_heartbeat=old_time),
        Trust(id=uuid4(), name="Trust B", last_heartbeat=old_time),
    ]


@pytest.fixture
def mock_trusts_no_heartbeat():
    """Trusts that have never sent a heartbeat."""
    return [
        Trust(id=uuid4(), name="Trust A", last_heartbeat=None),
        Trust(id=uuid4(), name="Trust B", last_heartbeat=None),
    ]


# ---- Tests ----


@pytest.mark.asyncio
async def test_check_trusts_health_online(authenticated, mock_trusts_online):
    """Trusts with recent heartbeats should be reported as online."""
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = mock_trusts_online

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get("/api/trust/health")

    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]["online"] is True
    assert response.json()[1]["online"] is True

    del app.dependency_overrides[get_session]


@pytest.mark.asyncio
async def test_check_trusts_health_stale_heartbeat(authenticated, mock_trusts_stale):
    """Trusts with stale heartbeats should be reported as offline."""
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = mock_trusts_stale

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get("/api/trust/health")

    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]["online"] is False
    assert response.json()[1]["online"] is False

    del app.dependency_overrides[get_session]


@pytest.mark.asyncio
async def test_check_trusts_health_no_heartbeat(authenticated, mock_trusts_no_heartbeat):
    """Trusts that have never sent a heartbeat should be reported as offline."""
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = mock_trusts_no_heartbeat

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get("/api/trust/health")

    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]["online"] is False
    assert response.json()[1]["online"] is False

    del app.dependency_overrides[get_session]


@pytest.mark.asyncio
async def test_check_trusts_health_mixed(authenticated):
    """Mix of online and offline trusts."""
    now = datetime.now(timezone.utc)
    trusts = [
        Trust(
            id=uuid4(), name="Online Trust",
            last_heartbeat=now - timedelta(seconds=5),
        ),
        Trust(
            id=uuid4(), name="Offline Trust",
            last_heartbeat=now - timedelta(minutes=5),
        ),
        Trust(id=uuid4(), name="Never Seen", last_heartbeat=None),
    ]

    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = trusts

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get("/api/trust/health")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    assert data[0]["online"] is True
    assert data[1]["online"] is False
    assert data[2]["online"] is False

    del app.dependency_overrides[get_session]


@pytest.mark.asyncio
async def test_check_trusts_health_no_trusts_found(authenticated):
    """No trusts in the database should return 404."""
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = []

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get("/api/trust/health")

    assert response.status_code == 404
    assert response.json() == {"detail": "No trusts found"}

    del app.dependency_overrides[get_session]


@pytest.mark.asyncio
async def test_check_trusts_health_internal_server_error(authenticated):
    """Database error should return 500."""
    mock_db = MagicMock()
    mock_db.exec.side_effect = Exception("Database error")

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get("/api/trust/health")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}

    del app.dependency_overrides[get_session]


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(mock_trusts_online):
    """No bearer token → 401 from ``verify_token``, and the roster never leaves the server.

    This is the security property: without it any anonymous caller could enumerate the
    participating trusts and see which are online. The DB is stubbed with real trusts so a
    regression would surface as their names in the body, not as a 404 masking the leak."""
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = mock_trusts_online
    app.dependency_overrides[get_session] = lambda: mock_db
    try:
        response = client.get("/api/trust/health")
    finally:
        del app.dependency_overrides[get_session]

    assert response.status_code == 401
    body = response.text
    assert "Trust A" not in body
    assert "Trust B" not in body
    assert "online" not in response.json()


@pytest.mark.asyncio
async def test_endpoint_is_accessible_to_authenticated_non_admin(authenticated, mock_trusts_online):
    """Any signed-in role reads the roster — the gate is authentication, not a permission.

    Mirrors the same guarantee on GET /trust: the Connection Status page is visible to
    Researcher and Viewer, so an admin-only gate here would break it for them."""
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = mock_trusts_online
    app.dependency_overrides[get_session] = lambda: mock_db
    try:
        response = client.get("/api/trust/health")
    finally:
        del app.dependency_overrides[get_session]

    assert response.status_code == 200
    assert {t["trustName"] for t in response.json()} == {"Trust A", "Trust B"}
