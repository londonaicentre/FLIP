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

"""Unit tests for the demo-video recorder's credential fallback, app-file listing and tutorial copy."""

import re
from pathlib import Path

import pytest
import requests

from flip_api.domain.schemas.projects import ProjectDetails
from flip_api.utils import constants
from tests import demo_video
from tests.demo_video import (
    APPS,
    REPO_ROOT,
    app_files_for,
    check_reused_project_matches_profile,
    resolve_ui_credentials,
)
from tests.e2e_smoke import TUTORIALS, SmokeFailure, describe_tutorial

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


def test_app_files_filters_hidden_pyc_and_blacklisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLACKLISTED_MODEL_FILES", "server_app.py, strategy.py")
    for name in ("client_app.py", "config.json", "server_app.py", ".hidden", "cached.pyc"):
        (tmp_path / name).write_text("content")
    (tmp_path / "subdir").mkdir()

    assert app_files_for(tmp_path) == ["client_app.py", "config.json"]


def test_app_files_empty_dir_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLACKLISTED_MODEL_FILES", "")
    with pytest.raises(SmokeFailure, match="No uploadable app files"):
        app_files_for(tmp_path)


APP_BACKENDS = [(app, backend) for app, profile in sorted(APPS.items()) for backend in sorted(profile["backends"])]


@pytest.mark.parametrize(("app", "backend"), APP_BACKENDS)
def test_every_recorded_app_resolves_to_a_known_tutorial(app: str, backend: str) -> None:
    """The recorder takes its project description from e2e_smoke's TUTORIALS, keyed off the app path.

    That key is a directory name in another tree (``fl-tutorials/``), so moving or renaming a tutorial
    would silently drop the recording to ``describe_tutorial``'s generic fallback — a demo video whose
    projects list reads "Training the ... application across the participating trusts' data." Pin the
    resolution instead of trusting it, and pin the description against the cap it is posted through.
    """
    app_dir = REPO_ROOT / APPS[app]["backends"][backend]["app_dir"]
    tutorial_dir = app_dir.parent.name

    assert tutorial_dir in TUTORIALS, (
        f"{app}/{backend} points at {tutorial_dir!r}, which TUTORIALS does not describe: the recording "
        "would fall back to generic copy"
    )
    copy = describe_tutorial(app_dir)
    assert copy is TUTORIALS[tutorial_dir]
    ProjectDetails(name=APPS[app]["project_name"], description=copy.task)


# ---------------------------------------------------------------------------------------------
# check_reused_project_matches_profile: a resumed project's has_imaging must agree with --app.
# ---------------------------------------------------------------------------------------------


def _stub_project_has_imaging(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setattr(demo_video.e2e_smoke, "project_has_imaging", lambda client, headers, project_id: value)


@pytest.mark.parametrize(("app", "project_imaging"), [("xray", True), ("spleen", True), ("ehr", False)])
def test_reused_project_matching_its_profile_passes(monkeypatch: pytest.MonkeyPatch, app: str, project_imaging: bool):
    _stub_project_has_imaging(monkeypatch, project_imaging)
    check_reused_project_matches_profile(requests.Session(), {}, "proj-1", app, APPS[app])  # no raise


def test_resuming_an_ehr_project_under_the_default_xray_profile_is_refused(monkeypatch: pytest.MonkeyPatch):
    """The trap the guard exists for: --app defaults to xray, so a bare --project-id resume of an EHR
    project would wait on an imaging import that never starts. Fail up front, naming the fix."""
    _stub_project_has_imaging(monkeypatch, False)
    with pytest.raises(SmokeFailure, match=r"has_imaging=false.*--app xray records a imaging study.*pass --app ehr"):
        check_reused_project_matches_profile(requests.Session(), {}, "proj-1", "xray", APPS["xray"])


def test_resuming_an_imaging_project_under_the_ehr_profile_is_refused(monkeypatch: pytest.MonkeyPatch):
    """The inverse would skip the imaging wait and upload the EHR app onto an imaging project."""
    _stub_project_has_imaging(monkeypatch, True)
    # The remedy names every imaging profile, so derive the list rather than pin today's roster.
    imaging_apps = " / ".join(sorted(name for name, profile in APPS.items() if profile.get("has_imaging", True)))
    with pytest.raises(
        SmokeFailure, match=rf"has_imaging=true.*tabular-only study.*pass --app {re.escape(imaging_apps)}"
    ):
        check_reused_project_matches_profile(requests.Session(), {}, "proj-1", "ehr", APPS["ehr"])
