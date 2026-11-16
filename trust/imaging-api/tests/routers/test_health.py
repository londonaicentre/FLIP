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

from imaging_api.routers import health as health_module
from imaging_api.routers.health import router


def _pyproject_version() -> str:
    with (Path(__file__).resolve().parents[2] / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


@pytest.fixture
def unreadable_version(request, monkeypatch, tmp_path):
    """Point the version lookup at ``request.param`` and clear its cache around the test.

    The param is either a Path, or TOML text written to a pyproject.toml in tmp_path.
    The lookup is ``lru_cache``d, so a test that skips the clear either reads a
    cached real version (proving nothing) or poisons every later test with a
    cached None.
    """
    path = request.param
    if isinstance(path, str):
        path = tmp_path / "pyproject.toml"
        path.write_text(request.param)
    monkeypatch.setattr(health_module, "_PYPROJECT_PATH", path)
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
async def test_health_reports_package_version(client, monkeypatch):
    monkeypatch.delenv("FLIP_RELEASE", raising=False)
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json()["version"] == _pyproject_version()


@pytest.mark.asyncio
async def test_health_reports_the_baked_release_over_the_package_version(client, monkeypatch):
    """A CI-built image carries FLIP_RELEASE; that is the build the hub's Connection Status
    should name, not the informational pyproject number two different builds can share (FLIP#1204)."""
    monkeypatch.setenv("FLIP_RELEASE", "v9.9.9")
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json()["version"] == "v9.9.9"


@pytest.mark.parametrize(
    "unreadable_version",
    [
        pytest.param(Path("/nonexistent/pyproject.toml"), id="file missing (broken image layout)"),
        pytest.param(Path(__file__), id="file unparsable as TOML"),
        # A valid TOML without [project].version is the KeyError arm of the same guard.
        pytest.param('[project]\nname = "imaging-api"\n', id="version key absent (partially-written pyproject)"),
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
