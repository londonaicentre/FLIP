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
"""``reconvert_nifti``: convert archived scans that have DICOM but no NIfTI, by bulk launch (FLIP#1456 G3).

The command runs against ``FakeXnat``, which answers the routes it uses with the shapes XNAT 1.10 and
container-service 3.8.1 gave on dev trust 1 (14 Oct 2026). A bulk launch creates one container per
target, and conversions move on each time a project's containers are listed: the container ends and,
on success, the scan gains its NIFTI resource in the same step. Reading scans before containers
would see that as a conversion that ended without NIfTI.
"""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from imaging_api.config import get_settings
from imaging_api.routers.schemas import Project
from imaging_api.scripts.reconvert_nifti import POLL_SECONDS, run, triggers_by_project
from imaging_api.services.projects import DCM2NIIX_SUBSCRIPTION_NAME

HUB_A = "0b9a4c3e-1d2f-4a5b-8c6d-7e8f9a0b1c2d"
HUB_B = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
HUB_C = "2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f6a"
COMMAND_ID, WRAPPER_ID = 4, 9  # distinct, so a launch on the command id cannot pass for the wrapper's
BASE = get_settings().XNAT_URL
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
SITE_LISTING = "/xapi/commands/available/site"


def _project(xnat_id: str, hub_id: str) -> Project:
    return Project(
        ID=xnat_id, secondary_ID=hub_id, name=xnat_id.lower(), pi_firstname="", pi_lastname="", URI=f"/x/{xnat_id}"
    )


PROJECTS = [_project("XA", HUB_A), _project("XB", HUB_B), _project("XC", HUB_C), _project("OPS", "not-a-hub-id")]


def uri(experiment: str, scan: str) -> str:
    return f"/archive/experiments/{experiment}/scans/{scan}"


def _scan_json(scan: str, labels: set[str]) -> dict[str, Any]:
    """A scan as ``GET /data/experiments/{id}?format=json`` nests it, with its resources as ``file``."""
    resources = [{"data_fields": {"label": label}} for label in sorted(labels)]
    return {"data_fields": {"ID": scan}, "children": [{"field": "file", "items": resources}]}


class Response:
    def __init__(self, status_code: int, body: Any = None, text: str = "") -> None:
        self.status_code, self._body, self.text = status_code, body, text or json.dumps(body)

    def json(self) -> Any:
        return self._body


class FakeXnat:
    """XNAT's projects, sessions, scans, containers and Container Service launches, in memory."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.containers: list[dict[str, Any]] = []
        self.triggers: list[dict[str, Any]] = []
        self.outcomes: dict[str, list[str]] = {}  # scan URI -> what each launch does: ok | fail | hang | lost
        self.launches: list[tuple[str, int, list[str]]] = []  # (project, wrapper id, targets) per bulk launch
        self.image = get_settings().DCM2NIIX_IMAGE
        self.refuse: set[str] = set()  # targets a bulk launch reports as failures
        self.errors: dict[str, int] = {}  # path -> how many more times it answers 500
        self.calls: list[tuple[str, str]] = []

    def session(self, project: str, experiment: str, scans: dict[str, set[str]], age: Any = timedelta(hours=2)) -> None:
        """An archived session; ``age`` is how long ago, or a raw insert_date string."""
        inserted = age if isinstance(age, str) else (NOW - age).strftime("%Y-%m-%d %H:%M:%S.000")
        self.sessions[experiment] = {"project": project, "label": f"ACC-{experiment}", "scans": scans, "at": inserted}

    def container(self, scan_uri: str, status: str, outcome: str | None = None) -> None:
        """A container already on the scan; with an ``outcome`` it is a conversion under way that ends so."""
        self.containers.append(self._container(scan_uri, status, outcome))

    def trigger(self, project: str, active: bool = True) -> None:
        self.triggers.append(
            {
                "id": len(self.triggers) + 1,
                "name": DCM2NIIX_SUBSCRIPTION_NAME,
                "active": active,
                "valid": True,
                "event-filter": {"project-ids": [project]},
            }  # fmt: skip
        )

    def _container(self, scan_uri: str, status: str, outcome: str | None) -> dict[str, Any]:
        return {
            "id": 100 + len(self.containers),
            "status": status,
            "status-time": "2026-10-14T10:00:00.000+0000",
            "project": self.sessions[scan_uri.split("/")[3]]["project"],
            "_outcome": outcome,
            "inputs": [
                {"type": "raw", "name": "scan", "value": scan_uri},
                {"type": "command", "name": "gzip", "value": "true"},
                {"type": "wrapper-external", "name": "scan", "value": scan_uri},
                {"type": "wrapper-derived", "name": "scan-dicoms", "value": scan_uri + "/resources/1"},
            ],
        }

    def _advance(self) -> None:
        for container in self.containers:
            if container["status"] == "Created" and container["_outcome"] in ("ok", "fail"):
                container["status"] = "Complete" if container["_outcome"] == "ok" else "Failed"
                if container["_outcome"] == "ok":
                    _, _, _, experiment, _, scan = container["inputs"][0]["value"].split("/")
                    self.sessions[experiment]["scans"][scan].add("NIFTI")

    def _error(self, path: str) -> Response | None:
        if self.errors.get(path):
            self.errors[path] -= 1
            return Response(502, None, "<html><title>502 Bad Gateway</title>" + "x" * 2000 + "</html>")
        return None

    def get(self, url: str, headers: Any = None, params: dict[str, str] | None = None, **_: Any) -> Response:
        path, query = self._split(url)
        query |= params or {}
        self.calls.append(("GET", path))
        if failed := self._error(path):
            return failed
        if path == "/xapi/events/subscriptions":
            return Response(200, self.triggers)
        if path == "/data/projects":
            return Response(200, {"ResultSet": {"Result": [p.model_dump(by_alias=True) for p in PROJECTS]}})
        if path == "/data/experiments":
            rows = [
                {
                    "ID": e,
                    "label": s["label"],
                    "date": "",
                    "project": s["project"],
                    "insert_date": s["at"],
                    "xsiType": "xnat:crSessionData",
                    "URI": f"/data/experiments/{e}",
                }  # fmt: skip
                for e, s in self.sessions.items()
                if s["project"] == query.get("project")
            ]
            return Response(200, {"ResultSet": {"Result": rows}})
        parts = path.strip("/").split("/")
        if parts[:2] == ["data", "experiments"] and len(parts) == 3:
            scans = [_scan_json(scan, labels) for scan, labels in self.sessions[parts[2]]["scans"].items()]
            return Response(200, {"items": [{"children": [{"field": "scans/scan", "items": scans}]}]})
        if parts[:2] == ["xapi", "projects"] and parts[3:] == ["containers"]:
            self._advance()
            return Response(200, [c for c in self.containers if c["project"] == parts[2]])
        if path == SITE_LISTING:
            row = {"command-id": COMMAND_ID, "wrapper-id": WRAPPER_ID, "wrapper-name": "dcm2niix-scan",
                   "image-name": self.image, "enabled": True}  # fmt: skip
            return Response(200, [row])
        raise AssertionError(f"unexpected GET {path}")

    def post(self, url: str, headers: Any = None, **kwargs: Any) -> Response:
        path, _ = self._split(url)
        self.calls.append(("POST", path))
        parts = path.strip("/").split("/")
        assert parts[:2] == ["xapi", "projects"], path
        assert parts[3:] == ["wrappers", parts[4], "root", "scan", "bulklaunch"], path
        assert isinstance(kwargs["json"]["scan"], str), "the targets must travel as one JSON string"
        assert set(kwargs["json"]) == {"scan"}, kwargs["json"]
        targets = json.loads(kwargs["json"]["scan"])
        self.launches.append((parts[2], int(parts[4]), targets))
        if failed := self._error(path):
            return failed
        for target in set(targets) - self.refuse:
            queued = self.outcomes.get(target)
            outcome = queued.pop(0) if queued else "ok"
            if outcome != "lost":  # lost: queued, but no container ever appears
                self.containers.append(self._container(target, "Created", outcome))
        failures = [{"message": "queue full", "params": {"scan": t}} for t in targets if t in self.refuse]
        return Response(200, {"bulk-launch-id": "bulk-svc123", "successes": [], "failures": failures})

    @staticmethod
    def _split(url: str) -> tuple[str, dict[str, str]]:
        assert url.startswith(BASE), url
        parts = urlsplit(url[len(BASE) :])
        return unquote(parts.path), {k: v[0] for k, v in parse_qs(parts.query).items()}


@pytest.fixture
def xnat() -> Iterator[FakeXnat]:
    fake = FakeXnat()
    with (
        patch("requests.get", side_effect=fake.get),
        patch("requests.put", side_effect=AssertionError("reconvert-nifti makes no PUT")),
        patch("requests.post", side_effect=fake.post),
    ):
        yield fake


def drive(argv: list[str]) -> tuple[int, list[str], float]:
    """Runs the command on a fake clock; returns (exit code, report lines, seconds it waited)."""
    lines: list[str] = []
    waited = [0.0]
    code = run(
        argv,
        {"Cookie": "x"},
        out=lines.append,
        sleep=lambda s: waited.__setitem__(0, waited[0] + s),
        clock=lambda: waited[0],
        now=lambda: NOW,
    )
    return code, lines, waited[0]


def launched(xnat: FakeXnat) -> list[str]:
    return [target for _, _, targets in xnat.launches for target in targets]


def test_without_launch_it_lists_the_scans_and_changes_nothing(xnat):
    xnat.trigger("XA")
    xnat.session("XA", "E1", {"1": {"DICOM"}, "2": {"DICOM", "NIFTI"}, "3": {"SNAPSHOTS"}})
    xnat.session("XA", "E2", {"5": {"DICOM"}})
    xnat.session("OPS", "E9", {"1": {"DICOM"}})

    code, lines, _ = drive([])

    assert code == 0
    assert [m for m, _ in xnat.calls if m != "GET"] == [], "a report changed something"
    assert lines[:3] == [
        f"XA (hub project {HUB_A}, 'xa'), trigger active: 2 of 3 archived scans with DICOM have no NIfTI.",
        "  ACC-E1 (E1) scan 1",
        "  ACC-E2 (E2) scan 5",
    ]
    assert "Would launch 2 ('dcm2niix-scan', wrapper 9)." in lines
    assert not any("E9" in line for line in lines), "a project FLIP did not create was reported"
    assert lines[-1] == "Projects: 3. Scans without NIfTI: 2. Would launch: 2. Not launched: 0. Failed to launch: 0."


def test_launch_converts_them_in_one_bulk_launch_per_project_and_follows_them(xnat):
    """Following reads containers before scans: the other way round, the fake's conversion looks failed."""
    xnat.session("XA", "E1", {"1": {"DICOM"}, "2": {"DICOM"}})
    xnat.session("XB", "E2", {"1": {"DICOM"}})

    code, lines, waited = drive(["--project", HUB_A, "--project", "XB", "--launch"])

    assert code == 0, lines
    assert xnat.launches == [("XA", WRAPPER_ID, [uri("E1", "1"), uri("E1", "2")]), ("XB", WRAPPER_ID, [uri("E2", "1")])]
    assert xnat.calls.count(("GET", SITE_LISTING)) == 1, "the wrapper is the site's: one lookup per run"
    assert all(xnat.sessions["E1"]["scans"][s] == {"DICOM", "NIFTI"} for s in ("1", "2"))
    assert "XA: launched 2 (bulk launch bulk-svc123)." in lines
    assert waited == POLL_SECONDS
    assert lines[-1].startswith("Projects: 2. Scans without NIfTI: 3. Launched: 3, 3 converted. Not launched: 0.")


def test_a_second_run_finds_nothing_to_do(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    assert drive(["--project", "XA", "--launch"])[0] == 0

    code, lines, _ = drive(["--project", "XA", "--launch"])

    assert code == 0
    assert len(xnat.launches) == 1
    assert "0 of 1 archived scans with DICOM have no NIfTI" in lines[0]


def test_without_project_it_launches_only_where_the_trigger_is_active(xnat):
    xnat.trigger("XA")
    xnat.trigger("XB", active=False)
    for project, experiment in (("XA", "E1"), ("XB", "E2"), ("XC", "E3")):
        xnat.session(project, experiment, {"1": {"DICOM"}})

    code, lines, _ = drive(["--launch"])

    assert code == 0
    assert [project for project, _, _ in xnat.launches] == ["XA"]
    assert "  Not launched: its trigger is inactive, so its 'DICOM to NIfTI' setting on the hub is off." in "\n".join(
        lines
    )
    assert any(line.startswith("  Not launched: it has no trigger") for line in lines)
    assert "Not launched: 2." in lines[-1]

    xnat.launches.clear()
    assert drive(["--project", "XB", "--project", HUB_C, "--launch"])[0] == 0
    assert sorted(project for project, _, _ in xnat.launches) == ["XB", "XC"], "naming a project overrides it"


# Finalizing is still writing the NIfTI, though XNAT's own "nonfinalized" filter leaves it out; a
# lowercase "complete" is the runtime's status before XNAT finalizes.
@pytest.mark.parametrize("status", ["_Waiting", "Running", "Finalizing", "complete", "Created"])
def test_a_scan_whose_container_has_not_finished_is_not_launched_twice(xnat, status):
    xnat.session("XA", "E1", {"1": {"DICOM"}, "2": {"DICOM"}})
    xnat.container(uri("E1", "1"), status)

    code, lines, _ = drive(["--project", "XA", "--launch"])

    assert code == 0
    assert launched(xnat) == [uri("E1", "2")]
    assert f"  ACC-E1 (E1) scan 1 [converting: container 100 {status} since 2026-10-14T10:00:00.000+0000]" in lines

    xnat.launches.clear()
    drive(["--project", "XA", "--launch", "--relaunch-unfinished"])
    assert launched(xnat) == [uri("E1", "1")], "--relaunch-unfinished must include it"


def test_a_conversion_ending_as_the_command_starts_is_not_launched_again(xnat):
    """It ends as the containers are first listed: a scans-first read would launch it a second time."""
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.container(uri("E1", "1"), "Created", outcome="ok")

    code, _, _ = drive(["--project", "XA", "--launch"])

    assert code == 0
    assert xnat.launches == []


@pytest.mark.parametrize(
    ("age", "left_alone"),
    [(timedelta(minutes=3), True), (timedelta(minutes=11), False), ("not a date", True)],
    ids=["3-min-old", "11-min-old", "unreadable"],
)
def test_a_session_archived_in_the_last_ten_minutes_is_left_to_its_trigger(xnat, age, left_alone):
    xnat.trigger("XA")
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.session("XA", "E2", {"1": {"DICOM"}}, age=age)

    code, lines, _ = drive(["--launch"])

    assert code == 0
    assert launched(xnat) == [uri("E1", "1")] + ([] if left_alone else [uri("E2", "1")])
    if left_alone:
        assert "1 session(s) archived in the last 10 min left alone." in lines[0]
        assert lines[-1].endswith("Recent sessions left alone: 1.")


def test_a_failed_conversion_is_left_to_the_next_run_up_to_the_bound(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.outcomes = {uri("E1", "1"): ["fail", "fail", "fail"]}

    for attempt in (1, 2, 3):
        code, lines, _ = drive(["--project", "XA", "--launch"])
        assert code == 1, f"attempt {attempt}"
        assert len(xnat.launches) == attempt, "relaunched within a run"
        assert "  ACC-E1 (E1) scan 1: ended without NIfTI" in lines

    code, lines, _ = drive(["--project", "XA", "--launch"])
    assert code == 0
    assert len(xnat.launches) == 3
    assert "  ACC-E1 (E1) scan 1 [3 conversions ended without NIfTI, not launched again]" in lines


def test_earlier_ended_conversions_count_towards_the_bound(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}, "2": {"DICOM"}})
    for status in ("Failed (Staging)", "Complete"):  # a Complete container on a scan without NIfTI failed too
        xnat.container(uri("E1", "1"), status)
    xnat.container(uri("E1", "2"), "Killed")

    code, lines, _ = drive(["--project", "XA", "--launch", "--max-attempts", "2"])

    assert code == 0
    assert launched(xnat) == [uri("E1", "2")]
    assert "  ACC-E1 (E1) scan 1 [2 conversions ended without NIfTI, not launched again]" in lines


@pytest.mark.parametrize(
    ("outcome", "state"), [("lost", "without a container yet"), ("hang", "converting")], ids=["lost", "running"]
)
def test_a_launch_not_finished_when_the_wait_ends_is_reported_as_it_is(xnat, outcome, state):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.outcomes = {uri("E1", "1"): [outcome]}

    code, lines, waited = drive(["--project", "XA", "--launch", "--wait", "5"])

    assert code == 3
    assert waited == 5 * 60
    assert f"  ACC-E1 (E1) scan 1: {state}" in lines
    assert f"Launched: 1, 1 {state}." in lines[-1]


def test_an_earlier_container_does_not_stand_in_for_this_runs_launch(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.container(uri("E1", "1"), "Failed")
    xnat.outcomes = {uri("E1", "1"): ["lost"]}

    code, lines, _ = drive(["--project", "XA", "--launch", "--wait", "2"])

    assert code == 3, "the earlier Failed container was taken for this launch's"
    assert "  ACC-E1 (E1) scan 1: without a container yet" in lines


def test_wait_zero_launches_and_says_it_did_not_follow(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})

    code, lines, waited = drive(["--project", "XA", "--launch", "--wait", "0"])

    assert (code, waited) == (3, 0)
    assert "Launched: 1, 1 not followed (--wait 0)." in lines[-1]


def test_a_target_xnat_does_not_queue_is_a_failure(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}, "2": {"DICOM"}})
    xnat.refuse = {uri("E1", "1")}

    code, lines, _ = drive(["--project", "XA", "--launch"])

    assert code == 1
    assert f"XA: XNAT did not queue {uri('E1', '1')}: queue full" in lines
    assert "XA: launched 1 (bulk launch bulk-svc123)." in lines
    assert lines[-1].endswith("Failed to launch: 1.")


def test_a_rejected_bulk_launch_is_a_failure_and_the_next_project_still_launches(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.session("XB", "E2", {"1": {"DICOM"}})
    xnat.errors = {f"/xapi/projects/XA/wrappers/{WRAPPER_ID}/root/scan/bulklaunch": 1}

    code, lines, _ = drive(["--project", "XA", "--project", "XB", "--launch"])

    assert code == 1
    assert any(
        line.startswith("XA: launching 1 FAILED: the bulk launch in XNAT project 'XA' failed: 502") for line in lines
    )
    assert xnat.sessions["E2"]["scans"]["1"] == {"DICOM", "NIFTI"}


def test_a_wrapper_on_another_image_is_named_once_and_nothing_is_launched(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.session("XB", "E2", {"1": {"DICOM"}})
    xnat.image = "xnat/dcm2niix:latest"

    code, lines, _ = drive(["--project", "XA", "--project", "XB", "--launch"])

    assert code == 1
    assert xnat.launches == []
    assert (
        sum(
            line.startswith("Cannot launch 2: XNAT has 0 'dcm2niix-scan' wrappers on the pinned")
            and "(registered images: xnat/dcm2niix:latest)" in line
            for line in lines
        )
        == 1
    )


def test_a_project_xnat_will_not_show_is_reported_and_the_rest_go_on(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.session("XB", "E2", {"1": {"DICOM"}})
    xnat.errors = {"/xapi/projects/XA/containers": 1}

    code, lines, _ = drive(["--project", "XA", "--project", "XB", "--launch"])

    assert code == 1
    (unread,) = [line for line in lines if "NOT READ" in line]
    assert "listing the containers of XNAT project 'XA' failed: 502" in unread
    assert len(unread) < 500, "the operator's terminal got the whole error page"
    assert launched(xnat) == [uri("E2", "1")]
    assert lines[-1].endswith("Projects not read: 1.")


def test_a_failed_check_while_following_is_tried_again(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    real_get = xnat.get
    checks = []

    def flaky(url: str, **kwargs: Any) -> Response:
        if url.endswith("/containers"):
            checks.append(url)
            if len(checks) == 2:  # the first check while following
                return Response(502, None, "Bad Gateway")
        return real_get(url, **kwargs)

    with patch("requests.get", side_effect=flaky):
        code, lines, waited = drive(["--project", "XA", "--launch"])

    assert code == 0
    assert waited == 2 * POLL_SECONDS
    assert any(line.startswith("  Checking XA failed, trying again in 30 s") for line in lines)


@pytest.mark.parametrize("argv", [["--project", "no-such-project", "--launch"], ["--project", "OPS"]])
def test_an_unknown_or_foreign_project_launches_nothing(xnat, argv):
    code, lines, _ = drive(argv)

    assert code == 2
    assert [m for m, _ in xnat.calls if m != "GET"] == []
    assert "Nothing was launched" in lines[-1]


@pytest.mark.parametrize("argv", [["--wait", "-1"], ["--max-attempts", "0"]])
def test_bounds_are_validated(argv):
    with pytest.raises(SystemExit):
        run(argv, {}, out=lambda _: None)


@pytest.mark.parametrize(
    ("response", "refusal"),
    [
        (MagicMock(status_code=403, text="<html>Forbidden</html>"), "event subscriptions failed: 403"),
        # XNAT lists null while the Event Service is off, which is not "no triggers".
        (MagicMock(status_code=200, json=MagicMock(return_value=None)), "Event Service is off"),
    ],
    ids=["refused", "event-service-off"],
)
def test_a_subscription_listing_it_cannot_read_is_an_error(response, refusal):
    with patch("requests.get", return_value=response), pytest.raises(Exception, match=refusal):
        triggers_by_project({})
