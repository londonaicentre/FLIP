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

import pytest
from fastapi import status

from fl_api.app import app
from fl_api.routers import system
from fl_api.utils.schemas import ClientInfoModel


@pytest.fixture(autouse=True)
def override_session(client, mock_session):
    """Route every system endpoint to conftest's ``mock_session``."""
    app.dependency_overrides[system.get_session] = lambda: mock_session
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------
# check_server_status
# ---------------------------------------------------------------------


def test_check_server_status(client, mock_session):
    """✅ GET /check_server_status should call check_server_status"""
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
    mock_session.check_client_status.return_value = [
        {"name": "site-1", "last_connect_time": 123.0, "status": "connected"},
        {"name": "site-2", "last_connect_time": 456.0, "status": "not set"},
    ]

    resp = client.get("/check_client_status")
    assert resp.status_code == status.HTTP_200_OK
    mock_session.check_client_status.assert_called_once_with(None)


def test_check_client_status_with_targets(client, mock_session):
    """✅ Should call check_client_status(targets) when target_type=client and targets given"""
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
    mock_session.get_connected_client_list.return_value = [
        ClientInfoModel(name="clientA", last_connect_time=123.0, status="connected"),
        ClientInfoModel(name="clientB", last_connect_time=456.0, status="disconnected"),
    ]

    resp = client.get("/get_connected_client_list")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()[0]["name"] == "clientA"
    assert resp.json()[0]["last_connect_time"] == 123.0
    assert resp.json()[0]["status"] == "connected"


# ---------------------------------------------------------------------
# get_working_directory
# ---------------------------------------------------------------------


def test_get_working_directory_success(client, mock_session):
    """✅ Should return working directory string on success."""
    mock_session.get_working_directory.return_value = "/workspace/client1"

    resp = client.get("/get_working_directory/client1")
    assert resp.status_code == status.HTTP_200_OK
    mock_session.get_working_directory.assert_called_once_with("client1")
    assert resp.text.strip('"') == "/workspace/client1"


# ---------------------------------------------------------------------
# shutdown_system
# ---------------------------------------------------------------------


def test_shutdown_system_success(client, mock_session):
    mock_session.shutdown_system.return_value = None

    resp = client.post("/shutdown_system")
    assert resp.status_code == status.HTTP_200_OK
    mock_session.shutdown_system.assert_called_once()


# ---------------------------------------------------------------------
# session errors
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "attr", "path"),
    [
        ("GET", "get_connected_client_list", "/get_connected_client_list"),
        ("GET", "get_working_directory", "/get_working_directory/client1"),
        ("POST", "shutdown_system", "/shutdown_system"),
    ],
)
def test_session_error_is_500(client, mock_session, method, attr, path):
    """🚫 A session exception on each route surfaces as HTTP 500 carrying the message."""
    getattr(mock_session, attr).side_effect = Exception("boom")

    resp = client.request(method, path)
    assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "boom" in resp.text
