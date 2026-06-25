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

from fl_api.app import app
from fl_api.core.dependencies import get_session
from fl_api.routers import system
from fl_api.utils.schemas import ClientInfoModel


@pytest.fixture(autouse=True)
def override_session(client):
    """Override get_session with a conditional MagicMock that matches NVFLARE behavior."""
    fake_session = MagicMock()

    def check_server_status_side_effect():
        return {"status": "running", "start_time": 123.0}

    def check_client_status_side_effect(targets):
        if targets is None:
            return [{"name": "site-1"}, {"name": "site-2"}]
        return [{"name": t} for t in targets]

    def get_system_info_side_effect():
        return {"system": "info"}

    fake_session.check_server_status.side_effect = check_server_status_side_effect
    fake_session.check_client_status.side_effect = check_client_status_side_effect
    fake_session.get_system_info.side_effect = get_system_info_side_effect

    app.dependency_overrides[get_session] = lambda: fake_session
    yield fake_session  # ✅ yield it so tests can access it
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------
# check_server_status
# ---------------------------------------------------------------------


def test_check_server_status(client, mock_session):
    """✅ GET /check_server_status should call check_server_status"""
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.check_server_status.return_value = {"status": "running", "start_time": 123.0}

    resp = client.get("/check_server_status")
    assert resp.status_code == status.HTTP_200_OK
    mock_session.check_server_status.assert_called_once()
    resp_json = resp.json()
    assert resp_json["status"] == "running"
    assert resp_json["start_time"] == 123.0


# ---------------------------------------------------------------------
# check_client_status
# ---------------------------------------------------------------------


def test_check_client_status_without_targets(client, mock_session):
    """✅ Should call check_client_status(None) when target_type=client and no targets given"""
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.check_client_status.return_value = [
        {"name": "site-1", "last_connect_time": 123.0, "status": "connected"},
        {"name": "site-2", "last_connect_time": 456.0, "status": "not set"},
    ]

    resp = client.get("/check_client_status")
    assert resp.status_code == status.HTTP_200_OK
    mock_session.check_client_status.assert_called_once_with(None)


def test_check_client_status_with_targets(client, mock_session):
    """✅ Should call check_client_status(targets) when target_type=client and targets given"""
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.check_client_status.return_value = [
        {"name": "site-1", "last_connect_time": 123.0, "status": "connected"},
    ]

    resp = client.get("/check_client_status", params={"targets": ["site-1"]})
    assert resp.status_code == status.HTTP_200_OK
    mock_session.check_client_status.assert_called_once_with(["site-1"])


# ---------------------------------------------------------------------
# get_connected_client_list
# ---------------------------------------------------------------------


def test_get_connected_client_list_success(client, mock_session):
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.get_connected_client_list.return_value = [
        ClientInfoModel(name="clientA", last_connect_time=123.0, status="connected"),
        ClientInfoModel(name="clientB", last_connect_time=456.0, status="disconnected"),
    ]

    resp = client.get("/get_connected_client_list")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()[0]["name"] == "clientA"
    assert resp.json()[0]["last_connect_time"] == 123.0
    assert resp.json()[0]["status"] == "connected"


def test_get_connected_client_list_failure(client, mock_session):
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.get_connected_client_list.side_effect = Exception("broken")

    resp = client.get("/get_connected_client_list")
    assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "broken" in resp.text


# ---------------------------------------------------------------------
# get_working_directory
# ---------------------------------------------------------------------


def test_get_working_directory_success(client, mock_session):
    """✅ Should return working directory string on success."""
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.get_working_directory.return_value = "/workspace/client1"

    resp = client.get("/get_working_directory/client1")
    assert resp.status_code == status.HTTP_200_OK
    mock_session.get_working_directory.assert_called_once_with("client1")
    assert resp.text.strip('"') == "/workspace/client1"


def test_get_working_directory_failure(client, mock_session):
    """🚫 Should raise HTTP 500 when session.get_working_directory throws."""
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.get_working_directory.side_effect = Exception("boom")

    resp = client.get("/get_working_directory/client1")
    assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "boom" in resp.text


# ---------------------------------------------------------------------
# shutdown_system
# ---------------------------------------------------------------------


def test_shutdown_system_success(client, mock_session):
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.shutdown_system.return_value = None

    resp = client.post("/shutdown_system")
    assert resp.status_code == status.HTTP_200_OK
    mock_session.shutdown_system.assert_called_once()


def test_shutdown_system_failure(client, mock_session):
    app.dependency_overrides[system.get_session] = lambda: mock_session
    mock_session.shutdown_system.side_effect = Exception("boom")

    resp = client.post("/shutdown_system")
    assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "boom" in resp.text
