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
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request

from flip_api.domain.interfaces.fl import IClientStatus
from flip_api.domain.schemas.status import ClientStatus
from flip_api.fl_services.get_net_status import get_net_status

TRUST_1_ID = uuid4()
TRUST_2_ID = uuid4()
TRUST_3_ID = uuid4()


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
def mock_fetch_client_status():
    with patch("flip_api.fl_services.get_net_status.fetch_client_status") as mock:
        mock.return_value = [
            IClientStatus(name="client1", status=ClientStatus.NO_JOBS.value),
            IClientStatus(name="client2", status=ClientStatus.NO_REPLY.value),
            IClientStatus(name="client3", status=ClientStatus.NO_REPLY.value),
        ]
        yield mock


@pytest.fixture
def mock_get_trusts():
    class Trust:
        def __init__(self, trust_id, name):
            self.id = trust_id
            self.name = name

    with patch("flip_api.fl_services.get_net_status.get_trusts") as mock:
        mock.return_value = [
            Trust(TRUST_1_ID, "client1"),
            Trust(TRUST_2_ID, "client2"),
            Trust(TRUST_3_ID, "client3"),
        ]
        yield mock


@pytest.fixture
def mock_get_slot_names_by_trust_ids():
    with patch("flip_api.fl_services.get_net_status.get_slot_names_by_trust_ids") as mock:
        mock.return_value = {TRUST_1_ID: "client1", TRUST_2_ID: "client2", TRUST_3_ID: "client3"}
        yield mock


@pytest.fixture
def mock_get_settings():
    with patch("flip_api.fl_services.get_net_status.get_settings") as mock:
        mock.return_value.FL_BACKEND = "nvflare"
        yield mock


def test_get_net_status_success(
    fake_request,
    mock_db,
    mock_get_net_by_name,
    mock_fetch_client_status,
    mock_get_trusts,
    mock_get_slot_names_by_trust_ids,
    mock_get_settings,
):
    result = get_net_status("net-name", fake_request, mock_db)
    assert result.name == "net-name"
    assert result.fl_backend == "nvflare"
    assert len(result.clients) == 3
    assert any(client.name == "client1" and client.online for client in result.clients)
    assert any(client.name == "client2" and not client.online for client in result.clients)
    assert any(client.name == "client3" and not client.online for client in result.clients)


def test_get_net_status_matches_client_via_slot_name_when_trust_renamed(
    fake_request,
    mock_db,
    mock_get_net_by_name,
    mock_get_trusts,
    mock_get_slot_names_by_trust_ids,
    mock_get_settings,
):
    # Trust display name overrides leave the FL identity on the slot, not the trust.
    # Matching must therefore use slot_name; the UI keeps showing the trust's friendly name.
    mock_get_trusts.return_value[0].name = "(Mock) GSTT"
    mock_get_slot_names_by_trust_ids.return_value = {
        TRUST_1_ID: "Trust_1",
        TRUST_2_ID: "client2",
        TRUST_3_ID: "client3",
    }
    with patch("flip_api.fl_services.get_net_status.fetch_client_status") as mock_clients:
        mock_clients.return_value = [IClientStatus(name="Trust_1", status=ClientStatus.NO_JOBS.value)]
        result = get_net_status("net-name", fake_request, mock_db)
    online = [c for c in result.clients if c.online]
    assert len(online) == 1
    assert online[0].name == "(Mock) GSTT"


def test_get_net_status_reports_flower_backend(
    fake_request,
    mock_db,
    mock_get_net_by_name,
    mock_fetch_client_status,
    mock_get_trusts,
    mock_get_slot_names_by_trust_ids,
    mock_get_settings,
):
    mock_get_settings.return_value.FL_BACKEND = "flower"
    result = get_net_status("net-name", fake_request, mock_db)
    assert result.fl_backend == "flower"


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
