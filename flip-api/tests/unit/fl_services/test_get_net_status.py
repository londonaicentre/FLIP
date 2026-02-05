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

from flip_api.domain.interfaces.fl import IClientStatus
from flip_api.domain.schemas.status import ClientStatus
from flip_api.fl_services.get_net_status import get_net_status


@pytest.fixture
def mock_db():
    with patch("flip_api.fl_services.get_net_status.get_session") as mock_get_session:
        mock_db = MagicMock()
        mock_get_session.return_value = mock_db
        yield mock_db


@pytest.fixture
def fake_request():
    req = MagicMock(spec=Request)
    req.scope = {"request_id": "req-id"}
    return req


@pytest.fixture
def mock_get_net_by_name():
    with patch("flip_api.fl_services.get_net_status.get_net_by_name") as mock:
        mock.return_value = MagicMock(endpoint="endpoint", name="net-name")
        yield mock


@pytest.fixture
def mock_get_status():
    with patch("flip_api.fl_services.get_net_status.fetch_client_status") as mock:
        mock.return_value = [
            IClientStatus(name="client1", online=True, status=ClientStatus.NO_JOBS),
            IClientStatus(name="client2", online=False, status=ClientStatus.NO_REPLY),
            IClientStatus(name="client3", online=False, status=ClientStatus.NO_REPLY),
        ]
        yield mock


@pytest.fixture
def mock_get_trusts():
    class Trust:
        def __init__(self, name):
            self.name = name

    with patch("flip_api.fl_services.get_net_status.get_trusts") as mock:
        mock.return_value = [Trust("client1"), Trust("client2"), Trust("client3")]
        yield mock


def test_get_net_status_success(fake_request, mock_db, mock_get_net_by_name, mock_get_status, mock_get_trusts):
    result = get_net_status("net-name", fake_request, mock_db)
    assert result.name == "net-name"
    assert len(result.clients) == 3
    assert any(client.name == "client1" and client.online for client in result.clients)
    assert any(client.name == "client2" and not client.online for client in result.clients)
    assert any(client.name == "client3" and not client.online for client in result.clients)


def test_get_net_status_net_not_found(fake_request, mock_db):
    with patch("flip_api.fl_services.get_net_status.get_net_by_name", return_value=None):
        with pytest.raises(HTTPException) as exc:
            get_net_status("unknown", fake_request, mock_db)
        assert exc.value.status_code == 404


def test_get_net_status_status_missing(fake_request, mock_db, mock_get_net_by_name):
    with patch("flip_api.fl_services.get_net_status.fetch_client_status", return_value=None):
        with pytest.raises(HTTPException) as exc:
            get_net_status("net-name", fake_request, mock_db)
        assert exc.value.status_code == 502


def test_get_net_status_unexpected_error(fake_request, mock_db, mock_get_net_by_name):
    mock_get_net_by_name.return_value = []
    mock_get_net_by_name.side_effect = Exception("boom")

    with pytest.raises(HTTPException) as exc:
        get_net_status("net-name", fake_request, mock_db)
    assert exc.value.status_code == 500
