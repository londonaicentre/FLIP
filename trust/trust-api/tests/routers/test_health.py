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

import asyncio
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trust_api.routers.health import router
from trust_api.services import hub_status
from trust_api.utils.background import reset_dead_background_tasks, watch_background_task


def _pyproject_version() -> str:
    with (Path(__file__).resolve().parents[2] / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


# Create a test FastAPI app and include the router
app = FastAPI()
app.include_router(router)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.asyncio
async def test_health_check(client):
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_reports_package_version(client, monkeypatch):
    """Same contract as the sibling trust services' /health (imaging-api, data-access-api)."""
    monkeypatch.delenv("FLIP_RELEASE", raising=False)
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json()["version"] == _pyproject_version()


@pytest.mark.asyncio
async def test_health_reports_what_the_hub_last_said_about_itself(client):
    """hub_version + hub_key_match come from the last heartbeat reply (FLIP#1204); the on-prem
    readiness checklist reads them to tell an operator their kit is stale before they upgrade."""
    hub_status.reset()
    body = client.get("/health/").json()
    assert body["hub_version"] is None
    assert body["hub_key_match"] is None

    with patch("trust_api.services.hub_status.aes_key_fingerprint", return_value="abcdef012345"):
        hub_status.record({"hub_version": "v0.7.0", "aes_key_fingerprint": "abcdef012345"})
    body = client.get("/health/").json()
    assert body["hub_version"] == "v0.7.0"
    assert body["hub_key_match"] is True
    assert body["hub_key_fingerprint"] == "abcdef012345"
    hub_status.reset()


@pytest.mark.asyncio
async def test_health_reports_the_baked_release_over_the_package_version(client, monkeypatch):
    """A CI-built image carries FLIP_RELEASE; that is the build the hub's Connection Status
    should name, not the informational pyproject number two different builds can share (FLIP#1204)."""
    monkeypatch.setenv("FLIP_RELEASE", "sha-abc1234")
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json()["version"] == "sha-abc1234"


@pytest.mark.asyncio
async def test_health_reports_degraded_when_a_background_task_died(client):
    """/health must not keep saying ok once the poller or collector is gone —
    otherwise a container that has silently stopped working stays in rotation."""
    reset_dead_background_tasks()

    async def explode():
        raise RuntimeError("boom")

    task = asyncio.create_task(explode(), name="health_collector")
    task.add_done_callback(watch_background_task)
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    try:
        response = client.get("/health/")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert response.json()["dead_tasks"] == ["health_collector"]
    finally:
        reset_dead_background_tasks()


@pytest.mark.asyncio
async def test_health_is_ok_with_no_dead_tasks(client):
    reset_dead_background_tasks()
    assert client.get("/health/").json()["status"] == "ok"
    assert client.get("/health/").json()["dead_tasks"] == []
