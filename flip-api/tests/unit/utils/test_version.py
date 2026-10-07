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
"""The hub's build identity: the CI-baked FLIP_RELEASE, else the pyproject version (FLIP#1204)."""

import tomllib
from pathlib import Path

import pytest

import flip_api.utils.version as version_module
from flip_api.utils.version import build_identity, service_version


@pytest.fixture(autouse=True)
def _clear_version_cache():
    """service_version is lru_cached; clear around every test so one failure case
    can't poison the others (or the real lookup)."""
    service_version.cache_clear()
    yield
    service_version.cache_clear()


def _pyproject_version() -> str:
    with (Path(__file__).resolve().parents[3] / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_reads_the_version_from_the_projects_pyproject():
    assert service_version() == _pyproject_version()


def test_returns_none_when_the_file_cannot_be_read(monkeypatch):
    monkeypatch.setattr(version_module, "_PYPROJECT_PATH", Path("/nonexistent/pyproject.toml"))
    assert service_version() is None


def test_build_identity_is_the_baked_release_when_the_image_carries_one(monkeypatch):
    monkeypatch.setenv("FLIP_RELEASE", "v9.9.9")
    assert build_identity() == "v9.9.9"


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_build_identity_falls_back_to_the_pyproject_version(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("FLIP_RELEASE", raising=False)
    else:
        monkeypatch.setenv("FLIP_RELEASE", value)
    assert build_identity() == _pyproject_version()
