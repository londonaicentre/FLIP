# Copyright (c) 2026 Flower Labs GmbH
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

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from fl_api import app as app_module
from fl_api.schemas import ClientInfoModel, ServerInfoModel

# ── check_server_status (the SuperLink's HTTP /health route, FLIP#1914) ───────


def test_check_server_status_running(client, monkeypatch):
    monkeypatch.setenv("SUPERLINK_ADDRESS", "superlink:8000")
    monkeypatch.setattr(app_module, "_check_health", lambda _url, _timeout: True)

    response = client.get("/check_server_status")

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    ServerInfoModel.model_validate(response.json())


def test_check_server_status_stopped(client, monkeypatch):
    monkeypatch.setenv("SUPERLINK_ADDRESS", "superlink:8000")
    monkeypatch.setattr(app_module, "_check_health", lambda _url, _timeout: False)

    response = client.get("/check_server_status")

    assert response.status_code == 200
    assert response.json()["status"] == "STOPPED"
    ServerInfoModel.model_validate(response.json())


def test_check_server_status_stopped_without_superlink_address(client, monkeypatch):
    monkeypatch.delenv("SUPERLINK_ADDRESS", raising=False)

    assert client.get("/check_server_status").json()["status"] == "STOPPED"


def test_health_url_is_https_only_with_root_certificates(monkeypatch):
    monkeypatch.setenv("SUPERLINK_ADDRESS", "superlink:8000")
    monkeypatch.delenv("SUPERLINK_ROOT_CERTIFICATES", raising=False)
    assert app_module._get_superlink_health_url() == "http://superlink:8000/health"

    monkeypatch.setenv("SUPERLINK_ROOT_CERTIFICATES", "/certs/ca.crt")
    assert app_module._get_superlink_health_url() == "https://superlink:8000/health"


@pytest.fixture
def health_server():
    """A local HTTP server whose /health answers with the status the test sets."""

    class Handler(BaseHTTPRequestHandler):
        answer = 200

        def do_GET(self):
            self.send_response(self.answer if self.path == "/health" else 404)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield Handler, f"http://127.0.0.1:{server.server_port}/health"
    server.shutdown()
    server.server_close()


def test_check_health_follows_the_health_route(health_server):
    handler, url = health_server
    assert app_module._check_health(url, 2.0) is True

    handler.answer = 503
    assert app_module._check_health(url, 2.0) is False


def test_check_health_false_when_unreachable():
    assert app_module._check_health("http://127.0.0.1:1/health", 0.5) is False


def test_check_health_false_when_ca_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERLINK_ROOT_CERTIFICATES", str(tmp_path / "absent.crt"))
    assert app_module._check_health("https://127.0.0.1:1/health", 0.5) is False


# ── register_node ─────────────────────────────────────────────────────────────


def test_register_node(client):
    app_module._node_trust_mapping.clear()

    response = client.post("/register_node", json={"name": "Trust_1", "node_id": "111"})

    assert response.status_code == 200
    assert response.json() == {"status": "registered"}
    assert app_module._node_trust_mapping == {"111": "Trust_1"}


def test_register_node_updates_existing(client):
    app_module._node_trust_mapping.clear()
    app_module._node_trust_mapping["111"] = "Trust_OLD"

    response = client.post("/register_node", json={"name": "Trust_1", "node_id": "111"})

    assert response.status_code == 200
    assert app_module._node_trust_mapping == {"111": "Trust_1"}


# ── check_client_status (federation list) ─────────────────────────────────────


def _federation_json(nodes: list[dict]) -> str:
    return json.dumps({"federation": {"nodes": nodes}})


def test_check_client_status_all_online(client, src_root, mock_flwr_run):
    app_module._node_trust_mapping.clear()
    app_module._node_trust_mapping["111"] = "Trust_1"
    app_module._node_trust_mapping["222"] = "Trust_2"

    mock_flwr_run(
        stdout=_federation_json(
            [
                {"node_id": "111", "owner": "none", "status": "online"},
                {"node_id": "222", "owner": "none", "status": "online"},
            ]
        )
    )

    response = client.get("/check_client_status")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    statuses = {item["name"]: item["status"] for item in payload}
    assert statuses == {"Trust_1": "CONNECTED", "Trust_2": "CONNECTED"}
    for item in payload:
        ClientInfoModel.model_validate(item)


def test_check_client_status_mixed_health(client, src_root, mock_flwr_run):
    app_module._node_trust_mapping.clear()
    app_module._node_trust_mapping["111"] = "Trust_1"
    app_module._node_trust_mapping["222"] = "Trust_2"

    mock_flwr_run(
        stdout=_federation_json(
            [
                {"node_id": "111", "owner": "none", "status": "online"},
                {"node_id": "222", "owner": "none", "status": "offline"},
            ]
        )
    )

    response = client.get("/check_client_status")

    assert response.status_code == 200
    statuses = {item["name"]: item["status"] for item in response.json()}
    assert statuses == {"Trust_1": "CONNECTED", "Trust_2": "DISCONNECTED"}


def test_check_client_status_node_missing_from_federation(client, src_root, mock_flwr_run):
    """A registered node that doesn't appear in the federation list is DISCONNECTED."""
    app_module._node_trust_mapping.clear()
    app_module._node_trust_mapping["111"] = "Trust_1"
    app_module._node_trust_mapping["222"] = "Trust_2"

    mock_flwr_run(
        stdout=_federation_json(
            [
                {"node_id": "111", "owner": "none", "status": "online"},
                # Trust_2 (node 222) is not in the list at all
            ]
        )
    )

    response = client.get("/check_client_status")

    assert response.status_code == 200
    statuses = {item["name"]: item["status"] for item in response.json()}
    assert statuses == {"Trust_1": "CONNECTED", "Trust_2": "DISCONNECTED"}


def test_check_client_status_with_targets(client, src_root, mock_flwr_run):
    app_module._node_trust_mapping.clear()
    app_module._node_trust_mapping["111"] = "Trust_1"
    app_module._node_trust_mapping["222"] = "Trust_2"

    mock_flwr_run(
        stdout=_federation_json(
            [
                {"node_id": "111", "owner": "none", "status": "online"},
                {"node_id": "222", "owner": "none", "status": "online"},
            ]
        )
    )

    response = client.get("/check_client_status", params=[("targets", "Trust_2")])

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0] == {"name": "Trust_2", "status": "CONNECTED"}


def test_check_client_status_unknown_target(client, src_root, mock_flwr_run):
    """A target not in the node mapping is reported as DISCONNECTED."""
    app_module._node_trust_mapping.clear()
    app_module._node_trust_mapping["111"] = "Trust_1"

    mock_flwr_run(
        stdout=_federation_json(
            [
                {"node_id": "111", "owner": "none", "status": "online"},
            ]
        )
    )

    response = client.get("/check_client_status", params={"targets": ["Trust_Missing"]})

    assert response.status_code == 200
    assert response.json() == [{"name": "Trust_Missing", "status": "DISCONNECTED"}]


def test_check_client_status_empty_mapping(client, src_root, mock_flwr_run):
    """When no nodes have registered, the response is an empty list."""
    app_module._node_trust_mapping.clear()

    mock_flwr_run(
        stdout=_federation_json(
            [
                {"node_id": "111", "owner": "none", "status": "online"},
            ]
        )
    )

    response = client.get("/check_client_status")

    assert response.status_code == 200
    assert response.json() == []


def test_check_client_status_with_preregistered_nodes(client, src_root, mock_flwr_run):
    """Nodes registered before SuperNodes connect (via register-supernode-keys.sh)
    are correctly resolved when the federation list later reports them online."""
    app_module._node_trust_mapping.clear()

    # Simulate what register-supernode-keys.sh does: register node mappings
    # before SuperNodes connect to the SuperLink
    client.post("/register_node", json={"name": "Trust_1", "node_id": "999"})
    client.post("/register_node", json={"name": "Trust_2", "node_id": "888"})

    # Now the federation list reports those nodes as online
    mock_flwr_run(
        stdout=_federation_json(
            [
                {"node_id": "999", "owner": "none", "status": "online"},
                {"node_id": "888", "owner": "none", "status": "online"},
            ]
        )
    )

    response = client.get("/check_client_status")

    assert response.status_code == 200
    statuses = {item["name"]: item["status"] for item in response.json()}
    assert statuses == {"Trust_1": "CONNECTED", "Trust_2": "CONNECTED"}


def test_check_client_status_preregistered_partial_online(client, src_root, mock_flwr_run):
    """Preregistered nodes show DISCONNECTED if not yet in the federation list."""
    app_module._node_trust_mapping.clear()

    client.post("/register_node", json={"name": "Trust_1", "node_id": "999"})
    client.post("/register_node", json={"name": "Trust_2", "node_id": "888"})

    # Only Trust_1's node is online; Trust_2 hasn't connected yet
    mock_flwr_run(
        stdout=_federation_json(
            [
                {"node_id": "999", "owner": "none", "status": "online"},
            ]
        )
    )

    response = client.get("/check_client_status")

    assert response.status_code == 200
    statuses = {item["name"]: item["status"] for item in response.json()}
    assert statuses == {"Trust_1": "CONNECTED", "Trust_2": "DISCONNECTED"}
