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
from flip_api.domain.schemas.status import ClientStatus
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
        )
        yield mock


@pytest.fixture
def mock_fetch_client_status():
    with patch("flip_api.fl_services.get_status.fetch_client_status") as mock:
        mock.return_value = [
            IClientStatus(name="trust-1", status="no_jobs"),
        ]
        yield mock


@pytest.fixture(autouse=True)
def auto_mock_get_trust_liveness_map():
    """Mock get_trust_liveness_map globally to avoid hitting real DB in tests.

    Default: all trusts are considered offline (matching original NO_REPLY behavior).
    Override with mock_liveness_map fixture in specific tests.
    """
    with patch(
        "flip_api.fl_services.get_status.get_trust_liveness_map",
        return_value={"trust-1": False, "trust-2": False},
    ) as mock:
        yield mock


@pytest.fixture
def mock_get_settings():
    with patch("flip_api.fl_services.get_status.get_settings") as mock:
        mock.return_value.FL_BACKEND = "nvflare"
        yield mock


def test_get_status_endpoint_success(
    fake_request,
    mock_db,
    mock_get_nets,
    mock_get_trusts,
    mock_fetch_server_status,
    mock_fetch_client_status,
    mock_get_settings,
):
    result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    assert len(result) == 1
    net = result[0]
    assert net.name == "net-1"
    assert net.fl_backend == "nvflare"
    assert net.online is True
    assert net.net_in_use is True
    assert net.registered_clients == 2
    assert len(net.clients) == 2
    assert any(c.name == "trust-1" and c.online for c in net.clients)
    assert any(c.name == "trust-2" and not c.online for c in net.clients)


def test_get_status_endpoint_reports_flower_backend(
    fake_request,
    mock_db,
    mock_get_nets,
    mock_get_trusts,
    mock_fetch_server_status,
    mock_fetch_client_status,
    mock_get_settings,
):
    mock_get_settings.return_value.FL_BACKEND = "flower"
    result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    assert result[0].fl_backend == "flower"


def test_get_status_endpoint_error(fake_request, mock_db):
    with patch("flip_api.fl_services.get_status.get_nets", side_effect=Exception("boom")):
        with pytest.raises(HTTPException) as exc:
            get_status_endpoint(fake_request, mock_db, user_id="user-1")
        assert exc.value.status_code == 500


def test_get_status_endpoint_server_status_none(
    fake_request, mock_db, mock_get_nets, mock_get_trusts, mock_fetch_server_status, mock_get_settings
):
    mock_fetch_server_status.return_value = None
    result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    assert len(result) == 1
    assert result[0].online is False
    assert result[0].fl_backend == "nvflare"
    assert result[0].clients == []


@pytest.fixture
def mock_liveness_map_trust1_online(auto_mock_get_trust_liveness_map):
    """Override default to make trust-1 online, trust-2 offline."""
    auto_mock_get_trust_liveness_map.return_value = {"trust-1": True, "trust-2": False}
    yield auto_mock_get_trust_liveness_map


def test_get_status_endpoint_client_status_none_fallback_to_heartbeat(
    fake_request,
    mock_db,
    mock_get_nets,
    mock_get_trusts,
    mock_fetch_server_status,
    mock_fetch_client_status,
    mock_get_settings,
    mock_liveness_map_trust1_online,
):
    """When fetch_client_status returns None, fall back to heartbeat checks."""
    mock_fetch_server_status.return_value = IServerStatus(
        status="stopped",
    )
    mock_fetch_client_status.return_value = None

    result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    assert len(result) == 1
    net = result[0]
    assert net.online is True  # Server is reachable, clients fall back to heartbeat
    assert net.fl_backend == "nvflare"
    assert len(net.clients) == 2
    # trust-1 has recent heartbeat -> online -> NO_JOBS
    trust_1 = next(c for c in net.clients if c.name == "trust-1")
    assert trust_1.status == ClientStatus.NO_JOBS.value
    assert trust_1.online is True
    # trust-2 has no recent heartbeat -> offline -> NO_REPLY
    trust_2 = next(c for c in net.clients if c.name == "trust-2")
    assert trust_2.status == ClientStatus.NO_REPLY.value
    assert trust_2.online is False


def test_get_status_endpoint_trust_not_in_client_list_fallback_to_heartbeat(
    fake_request,
    mock_db,
    mock_get_nets,
    mock_get_trusts,
    mock_fetch_server_status,
    mock_fetch_client_status,
    mock_get_settings,
    mock_liveness_map_trust1_online,
):
    """When a trust is not found in client statuses, fall back to heartbeat checks."""
    mock_fetch_server_status.return_value = IServerStatus(status="started")
    # Only trust-1 is in client statuses; trust-2 is missing
    mock_fetch_client_status.return_value = [
        IClientStatus(name="trust-1", status="CONNECTED"),
    ]

    result = get_status_endpoint(fake_request, mock_db, user_id="user-1")
    assert len(result) == 1
    net = result[0]
    assert net.online is True
    assert len(net.clients) == 2
    # trust-1 matched directly -> CONNECTED -> online
    trust_1 = next(c for c in net.clients if c.name == "trust-1")
    assert trust_1.status == "CONNECTED"
    assert trust_1.online is True
    # trust-2 not in client list -> heartbeat fallback -> NO_REPLY (no recent heartbeat)
    trust_2 = next(c for c in net.clients if c.name == "trust-2")
    assert trust_2.status == ClientStatus.NO_REPLY.value
    assert trust_2.online is False
