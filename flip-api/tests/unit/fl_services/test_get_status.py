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

from flip_api.domain.interfaces.fl import (
    IClientStatus,
    IServerStatus,
)
from flip_api.domain.schemas.types import FLBackend
from flip_api.fl_services.get_status import get_status_endpoint

TRUST_1_ID = uuid4()
TRUST_2_ID = uuid4()


@pytest.fixture
def mock_db():
    # get_status_endpoint receives the session as an argument, so the test passes a plain mock.
    return MagicMock()


@pytest.fixture
def fake_request():
    req = MagicMock(spec=Request)
    req.scope = {"request_id": "req-id"}
    return req


@pytest.fixture
def mock_get_nets():
    class Net:
        def __init__(self, name, endpoint, fl_backend):
            self.name = name
            self.endpoint = endpoint
            self.fl_backend = fl_backend

    with patch("flip_api.fl_services.get_status.get_nets") as mock:
        mock.return_value = [Net("net-1", "endpoint1", FLBackend.NVFLARE)]
        yield mock


@pytest.fixture
def mock_get_trusts():
    class Trust:
        def __init__(self, trust_id, name, code=None):
            self.id = trust_id
            self.name = name
            self.code = code

    with patch("flip_api.fl_services.get_status.get_trusts") as mock:
        mock.return_value = [Trust(TRUST_1_ID, "trust-1"), Trust(TRUST_2_ID, "trust-2")]
        yield mock


@pytest.fixture
def mock_get_slot_names_by_trust_ids():
    # Default: trust.name == slot_name (no rename). Tests that exercise the rename
    # case override this to make the trust's display name diverge from its slot.
    with patch("flip_api.fl_services.get_status.get_slot_names_by_trust_ids") as mock:
        mock.return_value = {TRUST_1_ID: "trust-1", TRUST_2_ID: "trust-2"}
        yield mock


@pytest.fixture
def mock_fetch_server_status():
    with patch("flip_api.fl_services.get_status.fetch_server_status") as mock:
        mock.return_value = IServerStatus(status="started")
        yield mock


@pytest.fixture
def mock_fetch_client_status():
    with patch("flip_api.fl_services.get_status.fetch_client_status") as mock:
        mock.return_value = [
            IClientStatus(name="trust-1", status="no_jobs"),
        ]
        yield mock


def test_get_status_endpoint_success(
    fake_request,
    mock_db,
    mock_get_nets,
    mock_get_trusts,
    mock_get_slot_names_by_trust_ids,
    mock_fetch_server_status,
    mock_fetch_client_status,
):
    result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    assert len(result) == 1
    net = result[0]
    assert net.name == "net-1"
    # Backend always comes from the net's seeded (canonical) value, not the server self-report.
    assert net.fl_backend == FLBackend.NVFLARE
    assert net.online is True
    assert net.net_in_use is True
    assert net.registered_clients == 2
    assert len(net.clients) == 2
    assert any(c.name == "trust-1" and c.online for c in net.clients)
    assert any(c.name == "trust-2" and not c.online for c in net.clients)


def test_get_status_endpoint_matches_client_via_slot_name_when_trust_renamed(
    fake_request,
    mock_db,
    mock_get_nets,
    mock_get_trusts,
    mock_get_slot_names_by_trust_ids,
    mock_fetch_server_status,
):
    # Simulates the seeded-rename case: Trust display name is "(Mock) GSTT",
    # but the FL net only knows the slot identity "Trust_1". The endpoint must
    # match on slot_name (not trust.name) and still surface the friendly name
    # to the UI as the client label.
    mock_get_trusts.return_value[0].name = "(Mock) GSTT"
    mock_get_slot_names_by_trust_ids.return_value = {TRUST_1_ID: "Trust_1", TRUST_2_ID: "trust-2"}
    with patch("flip_api.fl_services.get_status.fetch_client_status") as mock_clients:
        mock_clients.return_value = [IClientStatus(name="Trust_1", status="no_jobs")]
        result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    online_clients = [c for c in result[0].clients if c.online]
    assert len(online_clients) == 1
    assert online_clients[0].name == "(Mock) GSTT"


def test_get_status_endpoint_trust_with_no_slot_assignment_is_offline(
    fake_request,
    mock_db,
    mock_get_nets,
    mock_get_trusts,
    mock_get_slot_names_by_trust_ids,
    mock_fetch_server_status,
    mock_fetch_client_status,
):
    # An unassigned trust has no FL identity to compare against; even if a
    # client happens to share its name, it must show offline (NO_REPLY).
    mock_get_slot_names_by_trust_ids.return_value = {TRUST_2_ID: "trust-2"}
    result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    trust_1_entry = next(c for c in result[0].clients if c.name == "trust-1")
    assert trust_1_entry.online is False


def test_get_status_endpoint_reports_seeded_backend(
    fake_request,
    mock_db,
    mock_get_nets,
    mock_get_trusts,
    mock_get_slot_names_by_trust_ids,
    mock_fetch_server_status,
    mock_fetch_client_status,
):
    # The status reflects the net's seeded backend regardless of the live server status —
    # there is no runtime self-report reconciliation anymore.
    result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    assert result[0].fl_backend == FLBackend.NVFLARE


def test_get_status_endpoint_reports_flower_backend(
    fake_request,
    mock_db,
    mock_get_nets,
    mock_get_trusts,
    mock_get_slot_names_by_trust_ids,
    mock_fetch_server_status,
    mock_fetch_client_status,
):
    # A Flower-seeded net reports flower: the backend is the net's canonical seeded
    # value (FLNets.fl_backend), not the live server self-report.
    mock_get_nets.return_value[0].fl_backend = FLBackend.FLOWER
    result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    assert result[0].fl_backend == FLBackend.FLOWER


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
    assert result[0].fl_backend == FLBackend.NVFLARE
    assert result[0].clients == []


def test_get_status_endpoint_client_status_none(
    fake_request,
    mock_db,
    mock_get_nets,
    mock_get_trusts,
    mock_get_slot_names_by_trust_ids,
    mock_fetch_server_status,
    mock_fetch_client_status,
):
    mock_fetch_server_status.return_value = IServerStatus(status="stopped")
    mock_fetch_client_status.return_value = []

    result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    assert len(result) == 1
    assert result[0].online is False
    assert result[0].fl_backend == FLBackend.NVFLARE
    assert result[0].clients == []
