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

from fl_api import app as app_module
from fl_api.schemas import ClientInfoModel, ServerInfoModel

# ── check_server_status (unchanged — still uses gRPC health) ──────────────────


def test_check_server_status_running(client, monkeypatch):
    monkeypatch.setenv("SUPERLINK_HEALTH_ADDRESS", "superlink:9097")
    monkeypatch.setattr(app_module, "_check_health", lambda _address, _timeout: True)

    response = client.get("/check_server_status")

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    ServerInfoModel.model_validate(response.json())


def test_check_server_status_stopped(client, monkeypatch):
    monkeypatch.setenv("SUPERLINK_HEALTH_ADDRESS", "superlink:9097")
    monkeypatch.setattr(app_module, "_check_health", lambda _address, _timeout: False)

    response = client.get("/check_server_status")

    assert response.status_code == 200
    assert response.json()["status"] == "STOPPED"
    ServerInfoModel.model_validate(response.json())


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

    mock_flwr_run(stdout=_federation_json([
        {"node_id": "111", "owner": "none", "status": "online"},
        {"node_id": "222", "owner": "none", "status": "online"},
    ]))

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

    mock_flwr_run(stdout=_federation_json([
        {"node_id": "111", "owner": "none", "status": "online"},
        {"node_id": "222", "owner": "none", "status": "offline"},
    ]))

    response = client.get("/check_client_status")

    assert response.status_code == 200
    statuses = {item["name"]: item["status"] for item in response.json()}
    assert statuses == {"Trust_1": "CONNECTED", "Trust_2": "DISCONNECTED"}


def test_check_client_status_node_missing_from_federation(client, src_root, mock_flwr_run):
    """A registered node that doesn't appear in the federation list is DISCONNECTED."""
    app_module._node_trust_mapping.clear()
    app_module._node_trust_mapping["111"] = "Trust_1"
    app_module._node_trust_mapping["222"] = "Trust_2"

    mock_flwr_run(stdout=_federation_json([
        {"node_id": "111", "owner": "none", "status": "online"},
        # Trust_2 (node 222) is not in the list at all
    ]))

    response = client.get("/check_client_status")

    assert response.status_code == 200
    statuses = {item["name"]: item["status"] for item in response.json()}
    assert statuses == {"Trust_1": "CONNECTED", "Trust_2": "DISCONNECTED"}


def test_check_client_status_with_targets(client, src_root, mock_flwr_run):
    app_module._node_trust_mapping.clear()
    app_module._node_trust_mapping["111"] = "Trust_1"
    app_module._node_trust_mapping["222"] = "Trust_2"

    mock_flwr_run(stdout=_federation_json([
        {"node_id": "111", "owner": "none", "status": "online"},
        {"node_id": "222", "owner": "none", "status": "online"},
    ]))

    response = client.get("/check_client_status", params=[("targets", "Trust_2")])

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0] == {"name": "Trust_2", "status": "CONNECTED"}


def test_check_client_status_unknown_target(client, src_root, mock_flwr_run):
    """A target not in the node mapping is reported as DISCONNECTED."""
    app_module._node_trust_mapping.clear()
    app_module._node_trust_mapping["111"] = "Trust_1"

    mock_flwr_run(stdout=_federation_json([
        {"node_id": "111", "owner": "none", "status": "online"},
    ]))

    response = client.get("/check_client_status", params={"targets": ["Trust_Missing"]})

    assert response.status_code == 200
    assert response.json() == [{"name": "Trust_Missing", "status": "DISCONNECTED"}]


def test_check_client_status_empty_mapping(client, src_root, mock_flwr_run):
    """When no nodes have registered, the response is an empty list."""
    app_module._node_trust_mapping.clear()

    mock_flwr_run(stdout=_federation_json([
        {"node_id": "111", "owner": "none", "status": "online"},
    ]))

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
    mock_flwr_run(stdout=_federation_json([
        {"node_id": "999", "owner": "none", "status": "online"},
        {"node_id": "888", "owner": "none", "status": "online"},
    ]))

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
    mock_flwr_run(stdout=_federation_json([
        {"node_id": "999", "owner": "none", "status": "online"},
    ]))

    response = client.get("/check_client_status")

    assert response.status_code == 200
    statuses = {item["name"]: item["status"] for item in response.json()}
    assert statuses == {"Trust_1": "CONNECTED", "Trust_2": "DISCONNECTED"}
