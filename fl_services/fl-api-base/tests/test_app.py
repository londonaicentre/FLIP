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

from unittest.mock import MagicMock

import pytest

from fl_api.app import app
from fl_api.core.dependencies import get_session


@pytest.fixture(autouse=True)
def override_session(client):
    fake_session = MagicMock()
    app.dependency_overrides[get_session] = lambda: fake_session
    yield
    app.dependency_overrides.clear()


def test_index_endpoint(client):
    """Check that the / endpoint responds correctly."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Welcome to the FLIP FL API!" in response.json()["message"]


def test_health_endpoint(client):
    """Check that the /health endpoint responds correctly."""
    response = client.get("/health")
    assert response.status_code == 200
    assert "healthy" in response.json()["status"]


def test_startup_session_is_created(client):
    """Confirm the session is created once on startup."""
    from fl_api.app import app

    assert hasattr(app.state, "session")
    assert app.state.session is not None


def test_unknown_route_returns_404(client):
    """Sanity check for missing routes."""
    res = client.get("/nonexistent")
    assert res.status_code == 404
