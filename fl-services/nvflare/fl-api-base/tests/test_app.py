# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

import importlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

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


def test_health_names_the_build(client, monkeypatch):
    """/health reports the baked FLIP_RELEASE, so a running FL API can say which build it is."""
    monkeypatch.setenv("FLIP_RELEASE", "v9.9.9")
    assert client.get("/health").json()["version"] == "v9.9.9"


def test_openapi_names_the_same_build(monkeypatch):
    """FastAPI reads the version at app construction, so reload the app module under the release."""
    import fl_api.app as app_module

    monkeypatch.setenv("FLIP_RELEASE", "v9.9.9")
    try:
        importlib.reload(app_module)
        assert app_module.app.version == "v9.9.9"
    finally:
        monkeypatch.undo()
        importlib.reload(app_module)


def test_startup_session_is_created(client):
    """Confirm the session is created once on startup."""
    from fl_api.app import app

    assert hasattr(app.state, "session")
    assert app.state.session is not None


def test_unknown_route_returns_404(client):
    """Sanity check for missing routes."""
    res = client.get("/nonexistent")
    assert res.status_code == 404


def test_startup_reports_empty_bundle_allow_list():
    """Startup calls the once-per-process allow-list check, so the boot log carries the warning (FLIP#905)."""
    with (
        patch("fl_api.app.warn_if_bundle_url_allow_list_empty") as warn,
        patch("fl_api.app.create_fl_session", return_value=MagicMock()),
        TestClient(app),
    ):
        warn.assert_called_once_with()


def test_startup_refuses_a_malformed_bundle_allow_list(monkeypatch):
    """A BUNDLE_URL_ALLOWED_ORIGINS entry that is not a bare origin fails the boot, not the first fetch (#1291)."""
    monkeypatch.setenv("BUNDLE_URL_ALLOWED_ORIGINS", "s3.eu-west-2.amazonaws.com")
    with (
        patch("fl_api.app.create_fl_session", return_value=MagicMock()),
        pytest.raises(ValueError, match="not a bare scheme://host"),
        TestClient(app),
    ):
        pass
