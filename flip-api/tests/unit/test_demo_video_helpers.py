# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the demo-video recorder's credential fallback and app-file listing."""

from pathlib import Path

import pytest

from flip_api.utils import constants
from tests.demo_video import app_files_for, resolve_ui_credentials
from tests.e2e_smoke import SmokeFailure

CREDENTIAL_VARS = [
    "DEMO_RESEARCHER_EMAIL",
    "DEMO_RESEARCHER_PASSWORD",
    "DEMO_ADMIN_EMAIL",
    "DEMO_ADMIN_PASSWORD",
    "ADMIN_USER_PASSWORD",
]


@pytest.fixture(autouse=True)
def _clear_credential_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in CREDENTIAL_VARS:
        monkeypatch.delenv(var, raising=False)


def test_demo_users_used_when_fully_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_RESEARCHER_EMAIL", "researcher@example.com")
    monkeypatch.setenv("DEMO_RESEARCHER_PASSWORD", "researcher-pw")
    monkeypatch.setenv("DEMO_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "admin-pw")

    researcher, admin, fallback_roles = resolve_ui_credentials()

    assert researcher == ("researcher@example.com", "researcher-pw")
    assert admin == ("admin@example.com", "admin-pw")
    assert fallback_roles == []


def test_missing_researcher_falls_back_to_well_known_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "admin-pw")
    monkeypatch.setenv("ADMIN_USER_PASSWORD", "well-known-pw")

    researcher, admin, fallback_roles = resolve_ui_credentials()

    assert researcher == (constants.ADMIN_EMAIL_1, "well-known-pw")
    assert admin == ("admin@example.com", "admin-pw")
    assert fallback_roles == ["researcher"]


def test_missing_admin_falls_back_to_well_known_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_RESEARCHER_EMAIL", "researcher@example.com")
    monkeypatch.setenv("DEMO_RESEARCHER_PASSWORD", "researcher-pw")
    monkeypatch.setenv("ADMIN_USER_PASSWORD", "well-known-pw")

    researcher, admin, fallback_roles = resolve_ui_credentials()

    assert researcher == ("researcher@example.com", "researcher-pw")
    assert admin == (constants.ADMIN_EMAIL_1, "well-known-pw")
    assert fallback_roles == ["admin"]


def test_partial_demo_credentials_count_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # An email without its password (or vice versa) must not half-configure a persona.
    monkeypatch.setenv("DEMO_RESEARCHER_EMAIL", "researcher@example.com")
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "admin-pw")
    monkeypatch.setenv("ADMIN_USER_PASSWORD", "well-known-pw")

    researcher, admin, fallback_roles = resolve_ui_credentials()

    assert researcher == (constants.ADMIN_EMAIL_1, "well-known-pw")
    assert admin == (constants.ADMIN_EMAIL_1, "well-known-pw")
    assert fallback_roles == ["researcher", "admin"]


def test_no_usable_credentials_raises() -> None:
    with pytest.raises(SmokeFailure, match="No usable UI credentials"):
        resolve_ui_credentials()


def test_app_files_filters_hidden_pyc_and_blacklisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BLACKLISTED_MODEL_FILES", "server_app.py, strategy.py")
    for name in ("client_app.py", "config.json", "server_app.py", ".hidden", "cached.pyc"):
        (tmp_path / name).write_text("content")
    (tmp_path / "subdir").mkdir()

    assert app_files_for(tmp_path) == ["client_app.py", "config.json"]


def test_app_files_empty_dir_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLACKLISTED_MODEL_FILES", "")
    with pytest.raises(SmokeFailure, match="No uploadable app files"):
        app_files_for(tmp_path)
