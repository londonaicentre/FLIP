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

from fl_api import app as app_module
from fl_api.schemas import ClientInfoModel, ServerInfoModel


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


def test_check_client_status_without_targets_mixed_health(client, monkeypatch):
    monkeypatch.setenv(
        "SUPERNODE_HEALTH_ADDRESSES",
        "site-1=supernode-1:9098,site-2=supernode-2:9098",
    )

    def fake_check_health(address: str, _timeout: float) -> bool:
        return address == "supernode-1:9098"

    monkeypatch.setattr(app_module, "_check_health", fake_check_health)

    response = client.get("/check_client_status")

    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {"name": "site-1", "status": "CONNECTED"},
        {"name": "site-2", "status": "DISCONNECTED"},
    ]
    for item in payload:
        ClientInfoModel.model_validate(item)


def test_check_client_status_with_targets_preserves_order(client, monkeypatch):
    monkeypatch.setenv(
        "SUPERNODE_HEALTH_ADDRESSES",
        "site-1=supernode-1:9098,site-2=supernode-2:9098",
    )
    monkeypatch.setattr(app_module, "_check_health", lambda _address, _timeout: True)

    response = client.get("/check_client_status", params=[("targets", "site-2"), ("targets", "site-1")])

    assert response.status_code == 200
    payload = response.json()
    assert [item["name"] for item in payload] == ["site-2", "site-1"]
    assert all(item["status"] == "CONNECTED" for item in payload)


def test_check_client_status_unknown_target_returns_disconnected(client, monkeypatch):
    monkeypatch.setenv("SUPERNODE_HEALTH_ADDRESSES", "site-1=supernode-1:9098")

    calls: list[tuple[str, float]] = []

    def fake_check_health(address: str, timeout: float) -> bool:
        calls.append((address, timeout))
        return True

    monkeypatch.setattr(app_module, "_check_health", fake_check_health)

    response = client.get("/check_client_status", params={"targets": ["site-missing"]})

    assert response.status_code == 200
    assert response.json() == [{"name": "site-missing", "status": "DISCONNECTED"}]
    assert calls == []
