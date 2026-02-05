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

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from flip_api.domain.interfaces.fl import (
    IClientStatus,
    IServerStatus,
)
from flip_api.fl_services.get_status import get_status_endpoint


@pytest.fixture
def mock_db():
    with patch("flip_api.fl_services.run_jobs.get_session") as mock_get_session:
        mock_db = MagicMock()
        mock_get_session.return_value = mock_db
        yield mock_db


@pytest.fixture
def fake_request():
    req = MagicMock(spec=Request)
    req.scope = {"request_id": "req-id"}
    return req


@pytest.fixture
def mock_get_nets():
    class Net:
        def __init__(self, name, endpoint):
            self.name = name
            self.endpoint = endpoint

    with patch("flip_api.fl_services.get_status.get_nets") as mock:
        mock.return_value = [Net("net-1", "endpoint1")]
        yield mock


@pytest.fixture
def mock_get_trusts():
    class Trust:
        def __init__(self, name):
            self.name = name

    with patch("flip_api.fl_services.get_status.get_trusts") as mock:
        mock.return_value = [Trust("trust-1"), Trust("trust-2")]
        yield mock


@pytest.fixture
def mock_fetch_server_status():
    with patch("flip_api.fl_services.get_status.fetch_server_status") as mock:
        mock.return_value = IServerStatus(
            status="started",
            start_time=1750067408.3509645,
        )
        yield mock


@pytest.fixture
def mock_fetch_client_status():
    with patch("flip_api.fl_services.get_status.fetch_client_status") as mock:
        mock.return_value = [
            IClientStatus(name="trust-1", online=True, status="no_jobs", last_connected=1750086492.088829),
        ]
        yield mock


def test_get_status_endpoint_success(
    fake_request, mock_db, mock_get_nets, mock_get_trusts, mock_fetch_server_status, mock_fetch_client_status
):
    result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    assert len(result) == 1
    net = result[0]
    assert net.name == "net-1"
    assert net.online is True
    assert net.net_in_use is True
    assert net.registered_clients == 2
    assert len(net.clients) == 2
    assert any(c.name == "trust-1" and c.online for c in net.clients)
    assert any(c.name == "trust-2" and not c.online for c in net.clients)


def test_get_status_endpoint_error(fake_request, mock_db):
    with patch("flip_api.fl_services.get_status.get_nets", side_effect=Exception("boom")):
        with pytest.raises(HTTPException) as exc:
            get_status_endpoint(fake_request, mock_db, user_id="user-1")
        assert exc.value.status_code == 500


def test_get_status_endpoint_server_status_none(
    fake_request, mock_db, mock_get_nets, mock_get_trusts, mock_fetch_server_status
):
    mock_fetch_server_status.return_value = None
    result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    assert len(result) == 1
    assert result[0].online is False
    assert result[0].clients == []


def test_get_status_endpoint_client_status_none(
    fake_request, mock_db, mock_get_nets, mock_get_trusts, mock_fetch_server_status, mock_fetch_client_status
):
    mock_fetch_server_status.return_value = IServerStatus(
        status="stopped",
        start_time=1750067408.3509645,
    )
    mock_fetch_client_status.return_value = []

    result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    assert len(result) == 1
    assert result[0].online is False
    assert result[0].clients == []
