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

"""Unit tests for the demo-video recorder's credential fallback, app-file listing, tutorial copy, frame timing and
workspace reset."""

import json
from pathlib import Path
from urllib.parse import quote

import pytest
import requests

from flip_api.domain.schemas.projects import ProjectDetails
from flip_api.utils import constants
from tests import demo_recorder, demo_video, seed_demo_projects
from tests.demo_recorder import FrameSink
from tests.demo_segments import Persona, strip_licence_header
from tests.demo_video import (
    APPS,
    REPO_ROOT,
    app_files_for,
    check_reused_project_matches_profile,
    reset_researcher_workspace,
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
    with pytest.raises(SmokeFailure, match=r"has_imaging=true.*tabular-only study.*pass --app spleen / xray"):
        check_reused_project_matches_profile(requests.Session(), {}, "proj-1", "ehr", APPS["ehr"])


def test_licence_header_is_stripped_from_the_on_camera_query() -> None:
    sql = "-- Copyright (c) 2026\n-- Licensed under Apache\n\nSELECT 1\n-- trailing note\nFROM x"

    assert strip_licence_header(sql) == "SELECT 1\n-- trailing note\nFROM x"


def test_each_frame_is_held_until_the_next_and_the_last_until_the_stop(tmp_path: Path) -> None:
    sink = FrameSink(tmp_path)
    # Chromium's frame clock runs far from ours: only the differences between its timestamps count.
    for ts, arrival in [(500_000.0, 1_000.0), (500_040.0, 1_045.0), (502_040.0, 3_050.0)]:
        sink.frames.append({"file": f"{len(sink.frames):06d}.jpg", "ts": ts, "arrival": arrival})

    lines = sink.write_concat(stop_ms=9_050.0).read_text().splitlines()

    assert lines == [
        "file '000000.jpg'",
        "duration 0.0400",
        "file '000001.jpg'",
        "duration 2.0000",
        "file '000002.jpg'",
        "duration 6.0000",
        # The concat demuxer only honours the last duration when the last file is repeated.
        "file '000002.jpg'",
    ]


RESEARCHER = Persona("flip-demo-researcher@example.com", "pw", role="researcher")
RESEARCHER_ID = "11111111-1111-1111-1111-111111111111"
SOMEONE_ELSE = "22222222-2222-2222-2222-222222222222"
TRUSTS = [{"id": "t1", "code": "GSTT"}]


class FakeHub(requests.Session):
    """Paged GETs answered from a path→pages table; every DELETE is recorded."""

    def __init__(self, pages: dict[str, list[list[dict]]]) -> None:
        super().__init__()
        self.pages = pages
        self.deleted: list[str] = []
        self.seeded: list[tuple] = []

    def paged_get(self, client, path: str, headers) -> requests.Response:  # signature of tests.e2e_smoke._get
        prefix, _, query = path.partition("&pageNumber=")
        page = int(query.split("&")[0])
        response = requests.Response()
        response.status_code = 200
        rows = self.pages[prefix]
        response._content = json.dumps({"data": rows[page - 1], "totalPages": len(rows)}).encode()
        return response

    def delete(self, url, **kwargs) -> requests.Response:
        self.deleted.append(str(url).rsplit("/", 1)[-1])
        response = requests.Response()
        response.status_code = 204
        return response


def _hub(monkeypatch: pytest.MonkeyPatch, users: list[list[dict]], projects: list[list[dict]]) -> FakeHub:
    hub = FakeHub(
        {
            f"/users?search={quote(RESEARCHER.email)}": users,
            f"/projects?owner={RESEARCHER_ID}": projects,
        }
    )
    monkeypatch.setattr(seed_demo_projects, "_get", hub.paged_get)
    monkeypatch.setattr(demo_video, "sign_in_headers", lambda persona: {"authorization": f"Bearer {persona.email}"})
    monkeypatch.setattr(
        seed_demo_projects,
        "seed_catalogue",
        lambda client, headers, trusts, entries, owner: hub.seeded.append((trusts, owner)),
    )
    return hub


def test_workspace_reset_deletes_only_the_demo_researchers_own_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _hub(
        monkeypatch,
        users=[[{"id": SOMEONE_ELSE, "email": "flip-demo-researcher@example.com.other"}],
               [{"id": RESEARCHER_ID, "email": "FLIP-Demo-Researcher@example.com"}]],
        projects=[[{"id": "p1", "ownerId": RESEARCHER_ID}, {"id": "shared", "ownerId": SOMEONE_ELSE}],
                  [{"id": "p2", "ownerId": RESEARCHER_ID}]],
    )  # fmt: skip

    assert reset_researcher_workspace(hub, {}, RESEARCHER, TRUSTS) == 2
    assert hub.deleted == ["p1", "p2"]


def test_workspace_reset_keeps_the_researchers_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    catalogue_name = seed_demo_projects.CATALOGUE[0]["name"]
    hub = _hub(
        monkeypatch,
        users=[[{"id": RESEARCHER_ID, "email": RESEARCHER.email}]],
        projects=[[{"id": "take", "name": "Last take", "ownerId": RESEARCHER_ID},
                   {"id": "catalogue", "name": catalogue_name, "ownerId": RESEARCHER_ID}]],
    )  # fmt: skip

    assert reset_researcher_workspace(hub, {}, RESEARCHER, TRUSTS) == 1
    assert hub.deleted == ["take"]


def test_workspace_reset_seeds_the_catalogue_as_the_researcher(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _hub(monkeypatch, users=[[{"id": RESEARCHER_ID, "email": RESEARCHER.email}]], projects=[[]])

    reset_researcher_workspace(hub, {}, RESEARCHER, TRUSTS)

    owner = seed_demo_projects.Owner(RESEARCHER_ID, {"authorization": f"Bearer {RESEARCHER.email}"})
    assert hub.seeded == [(TRUSTS, owner)]


def test_workspace_reset_never_runs_for_the_fallback_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _hub(monkeypatch, users=[[]], projects=[[{"id": "admins-project", "ownerId": RESEARCHER_ID}]])
    fallback = Persona(constants.ADMIN_EMAIL_1, "pw", role="researcher", fallback=True)

    assert reset_researcher_workspace(hub, {}, fallback, TRUSTS) == 0
    assert hub.deleted == []
    assert hub.seeded == []


def test_workspace_reset_refuses_an_unknown_demo_researcher(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _hub(monkeypatch, users=[[{"id": SOMEONE_ELSE, "email": "someone@example.com"}]], projects=[[]])

    with pytest.raises(SmokeFailure, match="make demo-users"):
        reset_researcher_workspace(hub, {}, RESEARCHER, TRUSTS)
    assert hub.deleted == []
    assert hub.seeded == []


def test_workspace_reset_is_on_by_default_and_can_be_kept() -> None:
    assert demo_video.parse_args([]).keep_workspace is False
    assert demo_video.parse_args(["--keep-workspace"]).keep_workspace is True


def test_the_encode_is_capped_and_writes_the_take(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(demo_recorder.subprocess, "run", lambda cmd, **kwargs: calls.append(cmd))
    rec = demo_recorder.Take(page=None, frames_dir=tmp_path, scale=3)  # type: ignore[arg-type]
    rec.sink.frames.append({"file": "000000.jpg", "ts": 0.0, "arrival": 0.0})

    out = rec.encode(tmp_path / "segments" / "01-create-project.mp4")

    (cmd,) = calls
    # Uncapped, x264 at 4K reached 5.5 GB on the 32-core dev box.
    assert cmd[cmd.index("-threads") + 1] == str(demo_recorder.ENCODE_THREADS)
    assert cmd[cmd.index("-rc-lookahead") + 1] == "20"
    assert "scale=3840:2400" in cmd[cmd.index("-vf") + 1]
    assert cmd[-1] == str(out.resolve())


def test_a_take_with_no_frames_is_refused(tmp_path: Path) -> None:
    rec = demo_recorder.Take(page=None, frames_dir=tmp_path, scale=1)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="no frames"):
        rec.encode(tmp_path / "out.mp4")


class FakeClock:
    """Stands in for ``demo_video.time``: sleeping advances the clock instantly."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeXnat(requests.Session):
    """Answers XNAT GETs by URL fragment; container listings come from a script, one per poll."""

    def __init__(self, container_polls: list) -> None:
        super().__init__()
        self.container_polls = container_polls
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _json(body) -> requests.Response:
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode()
        return response

    def get(self, url, **kwargs) -> requests.Response:
        url = str(url)
        self.calls.append(("GET", url))
        if url.endswith("/containers"):
            poll = self.container_polls.pop(0) if len(self.container_polls) > 1 else self.container_polls[0]
            if isinstance(poll, Exception):
                raise poll
            return self._json([{"status": status} for status in poll])
        if "/data/projects?" in url:
            return self._json({"ResultSet": {"Result": [{"ID": "xp1", "secondary_ID": "flip-1"}]}})
        if "/experiments?" in url:
            return self._json({"ResultSet": {"Result": [{"ID": "XNAT_E1"}]}})
        return self._json({"ResultSet": {"Result": []}})  # assessors

    def delete(self, url, **kwargs) -> requests.Response:
        self.calls.append(("DELETE", str(url)))
        response = requests.Response()
        response.status_code = 200
        return response


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(demo_video, "time", fake)
    return fake


def _container_polls(xnat: FakeXnat) -> int:
    return sum(1 for _, url in xnat.calls if url.endswith("/containers"))


def test_the_xnat_wait_returns_once_the_project_conversions_drain(clock: FakeClock) -> None:
    xnat = FakeXnat([["Created", "Finalizing", "Complete"], ["Finalizing", "Complete"], ["Complete", "Failed"]])

    demo_video.wait_for_xnat_conversions(xnat, "http://xnat", "xp1", poll_s=10)

    assert _container_polls(xnat) == 3
    assert ("GET", "http://xnat/xapi/projects/xp1/containers") in xnat.calls


def test_a_stalled_container_queue_does_not_hold_the_take(clock: FakeClock, capsys) -> None:
    # A finalize stall leaves jobs in Finalizing for good.
    xnat = FakeXnat([["Finalizing"] * 14])

    demo_video.wait_for_xnat_conversions(xnat, "http://xnat", "xp1", timeout_s=900, stall_s=90, poll_s=10)

    assert 90 <= clock.now < 900
    assert "unchanged for 90s" in capsys.readouterr().out


def test_a_queue_still_moving_at_the_timeout_is_recorded_anyway(clock: FakeClock, capsys) -> None:
    xnat = FakeXnat([["Created"] * n for n in range(300, 0, -1)])

    demo_video.wait_for_xnat_conversions(xnat, "http://xnat", "xp1", timeout_s=120, stall_s=90, poll_s=10)

    assert clock.now == 120
    assert "still running after 120s" in capsys.readouterr().out


def test_container_query_errors_are_retried_not_taken_for_a_stall(clock: FakeClock) -> None:
    xnat = FakeXnat([requests.exceptions.ConnectionError()] * 12 + [[]])

    demo_video.wait_for_xnat_conversions(xnat, "http://xnat", "xp1", timeout_s=900, stall_s=90, poll_s=10)

    assert clock.now == 120  # twelve failed polls, then the empty queue
    assert _container_polls(xnat) == 13


def test_xnat_sessions_are_cleared_after_the_last_poll(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    # Any request after the sweep opens a session, which the browser's login then makes "2 sessions from one IP".
    xnat = FakeXnat([["Created"], []])
    monkeypatch.setattr(demo_video.requests, "Session", lambda: xnat)

    resolved = demo_video.resolve_xnat_ids("http://xnat", "admin", "pw", "flip-1")

    assert resolved == {"project": "xp1", "experiment": "XNAT_E1", "has_segmentation": ""}
    assert xnat.calls[-1] == ("DELETE", "http://xnat/xapi/users/active/admin")
    assert _container_polls(xnat) == 2


def test_workspace_reset_spares_kept_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _hub(
        monkeypatch,
        users=[[{"id": RESEARCHER_ID, "email": RESEARCHER.email}]],
        projects=[[{"id": "old-take", "ownerId": RESEARCHER_ID}, {"id": "monai-label", "ownerId": RESEARCHER_ID}]],
    )

    assert reset_researcher_workspace(hub, {}, RESEARCHER, TRUSTS, keep=["monai-label"]) == 1
    assert hub.deleted == ["old-take"]


def test_keep_project_is_repeatable() -> None:
    assert demo_video.parse_args([]).keep_project == []
    assert demo_video.parse_args(["--keep-project", "a", "--keep-project", "b"]).keep_project == ["a", "b"]
