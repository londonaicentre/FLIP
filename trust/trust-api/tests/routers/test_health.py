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

import tomllib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trust_api.routers.health import router


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
async def test_health_reports_package_version(client):
    """Same contract as the sibling trust services' /health (imaging-api, data-access-api)."""
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json()["version"] == _pyproject_version()
