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

import trust_api.utils.version as version_module
from trust_api.utils.version import service_version


@pytest.fixture(autouse=True)
def _clear_version_cache():
    """service_version is lru_cached; clear around every test so one failure case
    can't poison the others (or the real lookup)."""
    service_version.cache_clear()
    yield
    service_version.cache_clear()


def _pyproject_version() -> str:
    with (Path(__file__).resolve().parents[2] / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_reads_the_version_from_the_projects_pyproject():
    assert service_version() == _pyproject_version()


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        (Path("/nonexistent/pyproject.toml"), "file missing — a broken image layout"),
        (Path(__file__), "file present but not valid TOML"),
    ],
)
def test_returns_none_and_warns_when_the_file_cannot_be_read(path, reason, monkeypatch, caplog):
    """The version is cosmetic (the drawer renders "—"), so this must never raise.
    But an unreadable pyproject means a broken image, and the lru_cache makes the
    failure permanent for the process — so it has to say so at least once, or the
    operator has nothing to grep."""
    monkeypatch.setattr(version_module, "_PYPROJECT_PATH", path)

    with caplog.at_level("WARNING"):
        assert service_version() is None, reason

    assert any("Could not read the service version" in r.message for r in caplog.records)


def test_returns_none_when_the_version_key_is_absent(tmp_path, monkeypatch):
    """Valid TOML with no [project].version — the KeyError arm of the same guard."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "trust-api"\n')
    monkeypatch.setattr(version_module, "_PYPROJECT_PATH", pyproject)

    assert service_version() is None


def test_caches_so_a_broken_layout_is_reported_once(monkeypatch, caplog):
    """The cache is what keeps the warning from becoming per-request log spam."""
    monkeypatch.setattr(version_module, "_PYPROJECT_PATH", Path("/nonexistent/pyproject.toml"))

    with caplog.at_level("WARNING"):
        assert service_version() is None
        assert service_version() is None

    assert len([r for r in caplog.records if "Could not read the service version" in r.message]) == 1
