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

import tomllib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from data_access_api.routers import health as health_module
from data_access_api.routers.health import router


def _pyproject_version() -> str:
    with (Path(__file__).resolve().parents[2] / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


@pytest.fixture
def unreadable_version(request, monkeypatch):
    """Point the version lookup at ``path`` and clear its cache around the test.

    The lookup is ``lru_cache``d, so a test that skips the clear either reads a
    cached real version (proving nothing) or poisons every later test with a
    cached None.
    """
    monkeypatch.setattr(health_module, "_PYPROJECT_PATH", request.param)
    health_module._service_version.cache_clear()
    yield
    health_module._service_version.cache_clear()


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
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json()["version"] == _pyproject_version()


@pytest.mark.parametrize(
    "unreadable_version",
    [
        pytest.param(Path("/nonexistent/pyproject.toml"), id="file missing (broken image layout)"),
        pytest.param(Path(__file__), id="file unparsable as TOML"),
    ],
    indirect=True,
)
@pytest.mark.asyncio
async def test_health_still_answers_when_the_version_cannot_be_read(client, unreadable_version):
    """/health is a liveness probe first: an unreadable pyproject.toml must degrade
    to a null version, never a 500 that would take the container out of rotation."""
    response = client.get("/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": None}


@pytest.mark.asyncio
async def test_health_reports_null_version_when_the_key_is_absent(client, tmp_path, monkeypatch):
    """A valid TOML without [project].version (a partially-written pyproject) is the
    KeyError arm of the same guard."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "data-access-api"\n')
    monkeypatch.setattr(health_module, "_PYPROJECT_PATH", pyproject)
    health_module._service_version.cache_clear()
    try:
        assert client.get("/health/").json()["version"] is None
    finally:
        health_module._service_version.cache_clear()
