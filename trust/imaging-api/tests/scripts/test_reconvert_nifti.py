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

The command runs against ``FakeXnat``, which answers the routes it uses the way XNAT 1.10 with
container-service 3.8.1 does (shapes read live on dev trust 1, 14 Oct 2026). A bulk launch creates
one container per target, and conversions move on each time the project's containers are listed:
the container ends and, on success, the scan gains its NIFTI resource in the same step. That is
the race the command reads containers before scans to survive.
"""

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from imaging_api.config import get_settings
from imaging_api.routers.schemas import Project
from imaging_api.scripts.reconvert_nifti import POLL_SECONDS, run
from imaging_api.services.projects import DCM2NIIX_SUBSCRIPTION_NAME

HUB_A = "0b9a4c3e-1d2f-4a5b-8c6d-7e8f9a0b1c2d"
HUB_B = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
HUB_C = "2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f6a"
COMMAND_ID, WRAPPER_ID = 4, 9  # distinct, so a launch on the command id cannot pass for the wrapper's
BASE = get_settings().XNAT_URL


def _project(xnat_id: str, hub_id: str) -> Project:
    return Project(
        ID=xnat_id, secondary_ID=hub_id, name=xnat_id.lower(), pi_firstname="", pi_lastname="", URI=f"/x/{xnat_id}"
    )


PROJECTS = [_project("XA", HUB_A), _project("XB", HUB_B), _project("XC", HUB_C), _project("OPS", "not-a-hub-id")]


def uri(experiment: str, scan: str) -> str:
    return f"/archive/experiments/{experiment}/scans/{scan}"


class Response:
    def __init__(self, status_code: int, body: Any = None, text: str = "") -> None:
        self.status_code, self._body, self.text = status_code, body, text or json.dumps(body)

    def json(self) -> Any:
        return self._body


class FakeXnat:
    """XNAT's projects, sessions, scans, containers and Container Service launches, in memory."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}  # experiment -> {"project", "label", "scans": {id: labels}}
        self.containers: list[dict[str, Any]] = []
        self.triggers: list[dict[str, Any]] = []
        self.outcomes: dict[str, list[str]] = {}  # scan URI -> what each launch does: ok | fail | hang | lost
        self.launches: list[tuple[str, int, list[str]]] = []  # (project, wrapper id, targets) per bulk launch
        self.enabled: list[str] = []
        self.image = get_settings().DCM2NIIX_IMAGE
        self.refuse: set[str] = set()  # targets the bulk launch reports as failures
        self.refuse_relaunches = False  # report a target as a failure from its second launch on
        self.reject_after: int | None = None  # answer 500 to every bulk launch after this many
        self.calls: list[tuple[str, str]] = []

    # -- set-up ------------------------------------------------------------------------------
    def session(self, project: str, experiment: str, scans: dict[str, set[str]]) -> None:
        self.sessions[experiment] = {"project": project, "label": f"ACC-{experiment}", "scans": scans}

    def container(self, scan_uri: str, status: str, outcome: str | None = None) -> None:
        """A container already on the scan; with an ``outcome`` it is a conversion under way that ends so."""
        container = self._container(scan_uri, status)
        if outcome:
            container["_outcome"] = outcome
        self.containers.append(container)

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

    def _container(self, scan_uri: str, status: str) -> dict[str, Any]:
        project = self.sessions[scan_uri.split("/")[3]]["project"]
        return {
            "id": 100 + len(self.containers),
            "status": status,
            "status-time": "2026-10-14T10:00:00.000+0000",
            "project": project,
            "wrapper-id": WRAPPER_ID,
            "inputs": [
                {"type": "raw", "name": "scan", "value": scan_uri},
                {"type": "command", "name": "gzip", "value": "true"},
                {"type": "wrapper-external", "name": "scan", "value": scan_uri},
                {"type": "wrapper-derived", "name": "scan-dicoms", "value": scan_uri + "/resources/1"},
            ],
        }

    # -- what happens between checks ---------------------------------------------------------
    def _advance(self) -> None:
        for container in self.containers:
            outcome = container.get("_outcome")
            if container["status"] != "Created" or outcome in (None, "hang"):
                continue
            target = container["inputs"][0]["value"]
            if outcome == "ok":
                container["status"] = "Complete"
                _, _, _, experiment, _, scan = target.split("/")
                self.sessions[experiment]["scans"][scan].add("NIFTI")
            else:
                container["status"] = "Failed"

    # -- the routes ----------------------------------------------------------------------------
    def get(self, url: str, headers: Any = None, params: dict[str, str] | None = None, **_: Any) -> Response:
        path, query = self._split(url)
        query |= params or {}
        self.calls.append(("GET", path))
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
                    "insert_date": "",
                    "xsiType": "xnat:crSessionData",
                    "URI": f"/data/experiments/{e}",
                }  # fmt: skip
                for e, s in self.sessions.items()
                if s["project"] == query.get("project")
            ]
            return Response(200, {"ResultSet": {"Result": rows}})
        parts = path.strip("/").split("/")
        if parts[:2] == ["data", "experiments"] and len(parts) == 3:
            return Response(200, self._session_json(parts[2]))
        if parts[:2] == ["xapi", "projects"] and parts[3:] == ["containers"]:
            self._advance()
            return Response(200, [c for c in self.containers if c["project"] == parts[2]])
        if parts[:2] == ["xapi", "projects"] and parts[3:] == ["commands", "available"]:
            row = {"command-id": COMMAND_ID, "wrapper-id": WRAPPER_ID, "wrapper-name": "dcm2niix-scan",
                   "image-name": self.image, "enabled": True, "root-element-name": "scan"}  # fmt: skip
            return Response(200, [row])
        raise AssertionError(f"unexpected GET {path}")

    def put(self, url: str, headers: Any = None, **_: Any) -> Response:
        path, _query = self._split(url)
        self.calls.append(("PUT", path))
        assert path.endswith(f"/commands/{COMMAND_ID}/wrappers/dcm2niix-scan/enabled"), path
        self.enabled.append(path.split("/")[3])
        return Response(200, None, "")

    def post(self, url: str, headers: Any = None, **kwargs: Any) -> Response:
        path, _query = self._split(url)
        self.calls.append(("POST", path))
        parts = path.strip("/").split("/")
        assert parts[:2] == ["xapi", "projects"], path
        assert parts[3::2] == ["wrappers", "root", "bulklaunch"], path
        assert parts[6] == "scan", path
        body = kwargs["json"]
        assert set(body) == {"scan"}, body
        assert isinstance(body["scan"], str), "the targets must travel as one JSON string"
        targets = json.loads(body["scan"])
        before = set(launched(self))
        self.launches.append((parts[2], int(parts[4]), targets))
        if self.reject_after is not None and len(self.launches) > self.reject_after:
            return Response(500, None, "<html>HTTP Status 500</html>")
        failures, successes = [], []
        for target in targets:
            if target in self.refuse or (self.refuse_relaunches and target in before):
                failures.append({"message": "queue full", "params": {"scan": target}})
                continue
            successes.append({"workflow-id": "To be assigned", "params": {"scan": target}})
            queued = self.outcomes.get(target)
            outcome = queued.pop(0) if queued else "ok"
            if outcome != "lost":  # "lost": queued, but no container ever appears
                container = self._container(target, "Created")
                container["_outcome"] = outcome
                self.containers.append(container)
        return Response(200, {"bulk-launch-id": "bulk-svc123", "pipeline-name": "dcm2niix-scan",
                              "successes": successes, "failures": failures})  # fmt: skip

    def _session_json(self, experiment: str) -> dict[str, Any]:
        scans = [
            {"data_fields": {"ID": scan, "xnat_imagescandata_id": 1},
             "children": [{"field": "file", "items": [{"data_fields": {"label": label}} for label in sorted(labels)]}]}
            for scan, labels in self.sessions[experiment]["scans"].items()
        ]  # fmt: skip
        return {"items": [{"data_fields": {"ID": experiment}, "children": [{"field": "scans/scan", "items": scans}]}]}

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
        patch("requests.put", side_effect=fake.put),
        patch("requests.post", side_effect=fake.post),
    ):
        yield fake


def drive(argv: list[str]) -> tuple[int, list[str], float]:
    """Runs the command on a fake clock; returns (exit code, report lines, seconds it waited)."""
    lines: list[str] = []
    now = [0.0]
    code = run(
        argv, {"Cookie": "x"}, out=lines.append, sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0]
    )
    return code, lines, now[0]


def launched(xnat: FakeXnat) -> list[str]:
    return [target for _, _, targets in xnat.launches for target in targets]


def test_without_launch_it_lists_the_scans_and_changes_nothing(xnat):
    xnat.trigger("XA")
    xnat.session("XA", "E1", {"1": {"DICOM"}, "2": {"DICOM", "NIFTI"}, "3": {"SNAPSHOTS"}})
    xnat.session("XA", "E2", {"5": {"DICOM"}, "6": {"DICOM"}})
    xnat.session("OPS", "E9", {"1": {"DICOM"}})

    code, lines, _ = drive([])

    assert code == 0
    assert [m for m, _ in xnat.calls if m != "GET"] == [], "a report changed something"
    report = "\n".join(lines)
    assert f"XA (hub project {HUB_A}, 'xa'), trigger active: 3 of 4 archived scans with DICOM have no NIfTI." in report
    assert "  ACC-E1 (E1): scan 1\n" in report + "\n", "a scan with NIfTI, or without DICOM, was listed"
    assert "  ACC-E2 (E2): scans 5, 6" in report
    assert "Would launch 3 ('dcm2niix-scan', wrapper 9)." in report
    assert "E9" not in report, "a project FLIP did not create was reported"
    assert lines[-1] == "Projects: 3. Scans without NIfTI: 3. Would launch: 3. Failed to launch: 0. Not launched: 0."


def test_launch_converts_them_in_one_bulk_launch_and_follows_them_to_nifti(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}, "2": {"DICOM"}})
    xnat.session("XB", "E2", {"1": {"DICOM"}})

    code, lines, waited = drive(["--project", HUB_A, "--launch"])

    assert code == 0, lines
    assert xnat.enabled == ["XA"], "the wrapper must be enabled for the project before the launch"
    assert xnat.launches == [("XA", WRAPPER_ID, [uri("E1", "1"), uri("E1", "2")])]
    assert xnat.calls.index(("PUT", f"/xapi/projects/XA/commands/{COMMAND_ID}/wrappers/dcm2niix-scan/enabled")) < (
        xnat.calls.index(("POST", f"/xapi/projects/XA/wrappers/{WRAPPER_ID}/root/scan/bulklaunch"))
    )
    assert all(xnat.sessions["E1"]["scans"][s] == {"DICOM", "NIFTI"} for s in ("1", "2"))
    assert "  Launched 2 (bulk launch bulk-svc123)." in lines
    assert waited == POLL_SECONDS
    assert lines[-1].startswith("Projects: 1. Scans without NIfTI: 2. Launched: 2. Relaunched: 0. Converted: 2.")


def test_a_second_run_finds_nothing_to_do(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    assert drive(["--project", "XA", "--launch"])[0] == 0

    code, lines, _ = drive(["--project", "XA", "--launch"])

    assert code == 0
    assert len(xnat.launches) == 1
    assert any("all 1 archived scans with DICOM have NIfTI" in line for line in lines)


def test_without_project_it_launches_only_where_the_trigger_is_active(xnat):
    xnat.trigger("XA")
    xnat.trigger("XB", active=False)
    for project, experiment in (("XA", "E1"), ("XB", "E2"), ("XC", "E3")):
        xnat.session(project, experiment, {"1": {"DICOM"}})

    code, lines, _ = drive(["--launch"])

    assert code == 0
    assert [project for project, _, _ in xnat.launches] == ["XA"]
    report = "\n".join(lines)
    assert "Not launched: its trigger is inactive, so its 'DICOM to NIfTI' setting on the hub is off." in report
    assert "Not launched: it has no trigger" in report
    assert lines[-1].endswith("Failed to launch: 0. Not launched: 2.")


def test_naming_a_project_launches_there_whatever_its_trigger_says(xnat):
    xnat.trigger("XB", active=False)
    xnat.session("XB", "E2", {"1": {"DICOM"}})
    xnat.session("XC", "E3", {"1": {"DICOM"}})

    code, _, _ = drive(["--project", "XB", "--project", HUB_C, "--launch"])

    assert code == 0
    assert sorted(project for project, _, _ in xnat.launches) == ["XB", "XC"]


# Finalizing is still writing the NIfTI, though XNAT's own "nonfinalized" filter leaves it out; a
# lowercase "complete" is the runtime's status before XNAT finalizes.
@pytest.mark.parametrize("status", ["_Waiting", "Running", "Finalizing", "complete", "Created"])
def test_a_scan_whose_container_has_not_finished_is_not_launched_twice(xnat, status):
    xnat.session("XA", "E1", {"1": {"DICOM"}, "2": {"DICOM"}})
    xnat.container(uri("E1", "1"), status)

    code, lines, _ = drive(["--project", "XA", "--launch"])

    assert code == 0
    assert launched(xnat) == [uri("E1", "2")]
    assert any(
        f"scans 1 [converting: container 100 {status} since 2026-10-14T10:00:00.000+0000], 2" in x for x in lines
    )

    xnat.launches.clear()
    code, _, _ = drive(["--project", "XA", "--launch", "--relaunch-unfinished"])
    assert launched(xnat) == [uri("E1", "1")], "--relaunch-unfinished must include it"


def test_a_conversion_ending_as_the_command_starts_is_not_launched_again(xnat):
    """It ends as the containers are first listed: a scans-first read would launch it a second time."""
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.container(uri("E1", "1"), "Created", outcome="ok")

    code, lines, _ = drive(["--project", "XA", "--launch"])

    assert code == 0
    assert xnat.launches == []
    assert any("all 1 archived scans with DICOM have NIfTI" in line for line in lines)


def test_a_relaunch_still_waiting_for_its_container_is_not_doubled(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.outcomes = {uri("E1", "1"): ["fail", "lost"]}

    code, _, _ = drive(["--project", "XA", "--launch", "--wait", "3"])

    assert code == 3
    assert launched(xnat) == [uri("E1", "1")] * 2, "relaunched again before the relaunch had a container"


def test_an_earlier_container_does_not_stand_in_for_this_runs_launch(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.container(uri("E1", "1"), "Failed")
    xnat.outcomes = {uri("E1", "1"): ["lost"]}

    code, _, _ = drive(["--project", "XA", "--launch", "--wait", "2"])

    assert code == 3
    assert launched(xnat) == [uri("E1", "1")], "relaunched before its launch had a container"


def test_a_failed_conversion_is_relaunched_until_the_bound_then_given_up(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}, "2": {"DICOM"}})
    xnat.outcomes = {uri("E1", "1"): ["fail", "fail", "fail"], uri("E1", "2"): ["fail", "ok"]}

    code, lines, _ = drive(["--project", "XA", "--launch"])

    assert code == 1
    assert launched(xnat).count(uri("E1", "1")) == 3
    assert launched(xnat).count(uri("E1", "2")) == 2
    assert xnat.sessions["E1"]["scans"]["2"] == {"DICOM", "NIFTI"}
    assert any("scan 1: 3 conversions ended without NIfTI" in line and "not launched again" in line for line in lines)
    assert lines[-1].startswith(
        "Projects: 1. Scans without NIfTI: 2. Launched: 2. Relaunched: 3. Converted: 1. Still converting: 0. "
        "Ended without NIfTI: 1."
    )


def test_earlier_failed_conversions_count_towards_the_bound(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}, "2": {"DICOM"}})
    for status in ("Failed (Staging)", "Complete"):  # a Complete container on a scan without NIfTI failed too
        xnat.container(uri("E1", "1"), status)
    xnat.container(uri("E1", "2"), "Killed")
    xnat.outcomes = {uri("E1", "2"): ["fail"]}

    code, lines, _ = drive(["--project", "XA", "--launch", "--max-attempts", "2"])

    assert code == 1
    assert launched(xnat) == [uri("E1", "2")], "a scan with --max-attempts ended conversions was launched again"
    assert any("scans 1 [2 conversions ended without NIfTI, not launched again], 2" in line for line in lines)
    assert any("scan 2: 2 conversions ended without NIfTI" in line for line in lines)


def test_a_conversion_ending_between_the_two_reads_is_not_relaunched(xnat):
    """The fake ends the conversion as the containers are listed, so a scans-first read would see a
    Complete container on a scan without NIfTI and launch it again."""
    xnat.session("XA", "E1", {"1": {"DICOM"}})

    code, _, _ = drive(["--project", "XA", "--launch"])

    assert code == 0
    assert launched(xnat) == [uri("E1", "1")]


def test_a_launch_still_waiting_for_its_container_is_not_doubled_and_the_wait_is_bounded(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}, "2": {"DICOM"}})
    xnat.outcomes = {uri("E1", "1"): ["lost"], uri("E1", "2"): ["hang"]}

    code, lines, waited = drive(["--project", "XA", "--launch", "--wait", "5"])

    assert code == 3
    assert launched(xnat) == [uri("E1", "1"), uri("E1", "2")], "a queued or running launch was doubled"
    assert waited == 5 * 60
    assert "Still converting when the wait ended: 2." in "\n".join(lines)
    assert "  XA ACC-E1 (E1) scan 1" in lines


def test_wait_zero_launches_and_returns(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})

    code, lines, waited = drive(["--project", "XA", "--launch", "--wait", "0"])

    assert (code, waited) == (3, 0)
    assert "Not waiting (--wait 0): re-run the report to follow the conversions." in lines


def test_a_target_xnat_does_not_queue_is_a_failure(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}, "2": {"DICOM"}})
    xnat.refuse = {uri("E1", "1")}

    code, lines, _ = drive(["--project", "XA", "--launch"])

    assert code == 1
    assert f"  XNAT did not queue {uri('E1', '1')}: queue full" in lines
    assert "  Launched 1 (bulk launch bulk-svc123)." in lines


def test_a_bulk_launch_xnat_rejects_is_a_failure_and_the_rest_carry_on(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.session("XB", "E2", {"1": {"DICOM"}})
    xnat.reject_after = 0

    code, lines, _ = drive(["--project", "XA", "--project", "XB", "--launch"])

    assert code == 1
    assert [project for project, _, _ in xnat.launches] == ["XA", "XB"]
    assert sum("Cannot launch 1: Error: the bulk launch in XNAT project" in line for line in lines) == 2
    assert lines[-1].endswith("Failed to launch: 2. Not launched: 0.")


@pytest.mark.parametrize(("refuse", "reject"), [(True, None), (False, 1)], ids=["target-refused", "launch-rejected"])
def test_a_relaunch_xnat_will_not_take_is_a_failure_not_a_wait(xnat, refuse, reject):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.outcomes = {uri("E1", "1"): ["fail", "ok"]}
    xnat.refuse_relaunches, xnat.reject_after = refuse, reject

    code, lines, waited = drive(["--project", "XA", "--launch"])

    assert code == 1
    assert waited == POLL_SECONDS, "it kept waiting on a scan that was never relaunched"
    assert lines[-1].endswith("Still converting: 0. Ended without NIfTI: 0. Failed to launch: 1. Not launched: 0.")


def test_nothing_is_launched_when_every_scan_is_held_back(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.container(uri("E1", "1"), "Running")

    code, lines, _ = drive(["--project", "XA", "--launch"])

    assert code == 0
    assert xnat.launches == []
    assert "  Nothing to launch." in lines


def test_the_closing_list_of_scans_still_converting_is_capped(xnat):
    xnat.session("XA", "E1", {str(n): {"DICOM"} for n in range(25)})
    xnat.outcomes = {uri("E1", str(n)): ["hang"] for n in range(25)}

    code, lines, _ = drive(["--project", "XA", "--launch", "--wait", "1"])

    assert code == 3
    assert sum(line.startswith("  XA ACC-E1 (E1) scan ") for line in lines) == 20
    assert "  ... and 5 more." in lines


def test_a_wrapper_on_another_image_is_not_launched_and_the_rest_carry_on(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    xnat.session("XB", "E2", {"1": {"DICOM"}})
    xnat.image = "xnat/dcm2niix:latest"

    code, lines, _ = drive(["--project", "XA", "--project", "XB", "--launch"])

    assert code == 1
    assert xnat.launches == []
    assert xnat.enabled == []
    assert sum("Cannot launch 1: XNAT's 'dcm2niix-scan' wrapper (command 4) runs" in line for line in lines) == 2


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


def test_a_container_listing_failure_is_reported_bounded(xnat):
    xnat.session("XA", "E1", {"1": {"DICOM"}})
    tomcat_page = "<html><title>HTTP Status 403</title>" + "x" * 2000 + "</html>"
    real_get = xnat.get

    def refuse_containers(url: str, **kwargs: Any) -> Response:
        if url.endswith("/containers"):
            return Response(403, None, tomcat_page)
        return real_get(url, **kwargs)

    with (
        patch("requests.get", side_effect=refuse_containers),
        pytest.raises(Exception, match="containers of XNAT project 'XA' failed: 403") as excinfo,
    ):
        drive(["--project", "XA", "--launch"])

    assert xnat.launches == []
    assert len(str(excinfo.value)) < 400, "the operator's terminal got the whole Tomcat page"
