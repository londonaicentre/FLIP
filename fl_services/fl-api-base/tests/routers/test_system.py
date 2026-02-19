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

from unittest.mock import MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from fl_api.app import app
from fl_api.routers import system  # import router module to access get_session

client = TestClient(app)


@pytest.fixture
def mock_session():
    """Return a mock FLIP_Session object to override get_session."""
    return MagicMock(name="MockFLIPSession")


@pytest.fixture(autouse=True)
def clear_overrides():
    """Ensure dependency overrides are cleared after each test."""
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------
# check_status
# ---------------------------------------------------------------------


def test_check_status_server(mock_session):
    """✅ GET /check_status/server should call check_server_status"""
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.check_server_status.return_value = {"status": "running"}

    resp = client.get("/check_status/server")
    assert resp.status_code == status.HTTP_200_OK
    mock_session.check_server_status.assert_called_once()
    assert resp.json() == {"status": "running"}


def test_check_status_client_with_targets(mock_session):
    """✅ Should call check_client_status(targets) when target_type=client and targets given"""
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.check_client_status.return_value = [{"name": "site-1"}]

    resp = client.get("/check_status/client?targets=site-1&targets=site-2")
    assert resp.status_code == status.HTTP_200_OK
    mock_session.check_client_status.assert_called_once_with(["site-1", "site-2"])


def test_check_status_invalid_target_type(mock_session):
    """🚫 Invalid TargetType should return 422 (FastAPI validation error)"""
    app.dependency_overrides[system.get_session] = lambda: mock_session

    resp = client.get("/check_status/invalid")
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "target_type" in resp.text


# ---------------------------------------------------------------------
# cat_target
# ---------------------------------------------------------------------


def test_cat_target_success(mock_session):
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.cat_target.return_value = "file content"

    resp = client.get("/cat_target/site-1?file=log.txt")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.text.strip('"') == "file content"
    mock_session.cat_target.assert_called_once_with("site-1", options="", file="log.txt")


def test_cat_target_exception(mock_session):
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.cat_target.side_effect = Exception("boom")

    resp = client.get("/cat_target/site-1?file=log.txt")
    assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Error running cat command" in resp.text


# ---------------------------------------------------------------------
# get_connected_client_list
# ---------------------------------------------------------------------


def test_get_connected_client_list_success(mock_session):
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.get_connected_client_list.return_value = [{"name": "clientA"}]

    resp = client.get("/get_connected_client_list")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == [{"name": "clientA"}]


def test_get_connected_client_list_failure(mock_session):
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.get_connected_client_list.side_effect = Exception("broken")

    resp = client.get("/get_connected_client_list")
    assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Error getting connected client list" in resp.text


# ---------------------------------------------------------------------
# get_working_directory
# ---------------------------------------------------------------------


def test_get_working_directory_success(mock_session):
    """✅ Should return working directory string on success."""
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.get_working_directory.return_value = "/workspace/client1"

    resp = client.get("/get_working_directory/client1")
    assert resp.status_code == status.HTTP_200_OK
    mock_session.get_working_directory.assert_called_once_with("client1")
    assert resp.text.strip('"') == "/workspace/client1"


def test_get_working_directory_failure(mock_session):
    """🚫 Should raise HTTP 500 when session.get_working_directory throws."""
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.get_working_directory.side_effect = Exception("boom")

    resp = client.get("/get_working_directory/client1")
    assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Error getting working directory" in resp.text


# ---------------------------------------------------------------------
# set_timeout
# ---------------------------------------------------------------------


def test_set_timeout_success(mock_session):
    """✅ Should call session.set_timeout() and return its value."""
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.set_timeout.return_value = {"timeout": 15.0}

    resp = client.post("/set_timeout/15.0")
    assert resp.status_code == status.HTTP_200_OK
    mock_session.set_timeout.assert_called_once_with(15.0)
    assert resp.json() == {"timeout": 15.0}


def test_set_timeout_failure(mock_session):
    """🚫 Should return HTTP 500 if session.set_timeout raises."""
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.set_timeout.side_effect = Exception("invalid timeout")

    resp = client.post("/set_timeout/15.0")
    assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Error setting timeout" in resp.text


# ---------------------------------------------------------------------
# wait_until_server_status
# ---------------------------------------------------------------------


def test_wait_until_server_status_success(mock_session):
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.wait_until_server_status.return_value = {"status": "SUCCESS"}

    resp = client.get("/wait_until_server_status?interval=5&timeout=10")
    assert resp.status_code == status.HTTP_200_OK
    mock_session.wait_until_server_status.assert_called_once_with(interval=5, timeout=10, fail_attempts=3)
    assert resp.json() == {"status": "SUCCESS"}


def test_wait_until_server_status_error(mock_session):
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.wait_until_server_status.side_effect = Exception("boom")

    resp = client.get("/wait_until_server_status")
    assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Error waiting for server status" in resp.text


# ---------------------------------------------------------------------
# shutdown_system
# ---------------------------------------------------------------------


def test_shutdown_system_success(mock_session):
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.shutdown_system.return_value = {"status": "shut down"}

    resp = client.post("/shutdown_system")
    assert resp.status_code == status.HTTP_200_OK
    mock_session.shutdown_system.assert_called_once()


def test_shutdown_system_failure(mock_session):
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.shutdown_system.side_effect = Exception("boom")

    resp = client.post("/shutdown_system")
    assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Error shutting down system" in resp.text
