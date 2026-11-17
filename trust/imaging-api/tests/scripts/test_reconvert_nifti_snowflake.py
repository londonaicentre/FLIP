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
"""``reconvert_nifti`` on the Snowflake conversion path (FLIP#1583).

The selection is the same on both backends, because it reads XNAT and nothing else. What
these pin is the half that differs: the Container Service wrapper lookup and bulk launch
must not happen, APP.CONVERT_SCAN must be CALLed once per scan, and the output must be
registered in XNAT by this script rather than by a container service upload step that
does not exist here.

The XNAT side is a small fake over ``requests``; the converter is a stub, so nothing here
touches Snowflake, a token or a network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import patch

import pytest

from imaging_api.config import get_settings
from imaging_api.scripts.reconvert_nifti import run
from imaging_api.services.projects import DCM2NIIX_SUBSCRIPTION_NAME
from imaging_api.services.snowflake_converter import (
    DONE,
    FAILED,
    RUNNING,
    ConversionError,
    ScanRef,
    SnowflakeConverter,
)

HUB_A = "0b9a4c3e-1d2f-4a5b-8c6d-7e8f9a0b1c2d"
BASE = get_settings().XNAT_URL
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
ARCHIVED = "2026-10-14 10:00:00.0"


class Response:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload


class FakeXnat:
    """Just enough XNAT for the selection: one project, one session, one unconverted scan."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.nifti: set[str] = set()

    def get(self, url: str, **_: Any) -> Response:
        self.calls.append(("GET", url))
        path = url[len(BASE) :].split("?")[0]
        if path == "/data/projects":
            project = {
                "ID": "XA",
                "secondary_ID": HUB_A,
                "name": "xa",
                "pi_firstname": "A",
                "pi_lastname": "B",
                "URI": "/data/projects/XA",
            }
            return Response(200, {"ResultSet": {"Result": [project]}})
        if path == "/xapi/events/subscriptions":
            return Response(
                200, [{"name": DCM2NIIX_SUBSCRIPTION_NAME, "active": True, "event-filter": {"project-ids": ["XA"]}}]
            )
        if path == "/data/experiments":
            experiment = {
                "ID": "E1",
                "label": "ACC-E1",
                "date": "2026-10-14",
                "project": "XA",
                "insert_date": ARCHIVED,
                "xsiType": "xnat:mrSessionData",
                "URI": "/data/experiments/E1",
            }
            return Response(200, {"ResultSet": {"Result": [experiment]}})
        if path == "/data/experiments/E1":
            resources = [{"data_fields": {"label": "DICOM"}}]
            if "E1/3" in self.nifti:
                resources.append({"data_fields": {"label": "NIFTI"}})
            return Response(
                200,
                {
                    "items": [
                        {
                            "children": [
                                {
                                    "field": "scans/scan",
                                    "items": [
                                        {
                                            "data_fields": {"ID": "3"},
                                            "children": [{"field": "file", "items": resources}],
                                        }
                                    ],
                                }
                            ]
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected GET {url}")


class StubConverter:
    """Records launches and answers statuses from a script, as SnowflakeConverter would."""

    def __init__(self, statuses: list[str], collect: Any = 2) -> None:
        self.launched: list[ScanRef] = []
        self.collected: list[ScanRef] = []
        self.statuses = statuses
        self._collect = collect
        self.refuse: set[str] = set()

    def launch(self, scan: ScanRef) -> str:
        if scan.scan in self.refuse:
            raise ConversionError(f"APP.CONVERT_SCAN answered 'rejected' for {scan}")
        self.launched.append(scan)
        return "launched"

    def status(self, scan: ScanRef) -> str:
        return self.statuses.pop(0) if self.statuses else DONE

    def collect(self, scan: ScanRef, headers: dict[str, str]) -> int:
        self.collected.append(scan)
        if isinstance(self._collect, Exception):
            raise self._collect
        return int(self._collect)


@pytest.fixture
def xnat() -> Iterator[FakeXnat]:
    fake = FakeXnat()
    with (
        patch("requests.get", side_effect=fake.get),
        patch("requests.post", side_effect=AssertionError("the Snowflake path makes no POST to XNAT")),
        patch("imaging_api.scripts.reconvert_nifti.snowflake_conversion", return_value=True),
    ):
        yield fake


def drive(argv: list[str], converter: StubConverter) -> tuple[int, list[str]]:
    lines: list[str] = []
    elapsed = [0.0]
    code = run(
        argv,
        {"Cookie": "x"},
        out=lines.append,
        sleep=lambda s: elapsed.__setitem__(0, elapsed[0] + s),
        clock=lambda: elapsed[0],
        now=lambda: NOW,
        # A stub, not a SnowflakeConverter: everything run() uses of it is the three
        # methods below, and a real one would need an account to construct.
        converter=cast("SnowflakeConverter", converter),
    )
    return code, lines


def test_a_report_launches_nothing_and_names_the_procedure(xnat: FakeXnat) -> None:
    converter = StubConverter([])

    code, lines = drive([], converter)

    assert code == 0
    assert converter.launched == []
    assert "Would launch 1 (APP.CONVERT_SCAN)." in lines


def test_launching_calls_the_procedure_once_per_scan(xnat: FakeXnat) -> None:
    """No bulk call exists: APP.CONVERT_SCAN takes one scan, and a second procedure whose
    only content is a loop would be a second thing to keep correct."""
    converter = StubConverter([DONE])

    code, lines = drive(["--launch"], converter)

    assert code == 0
    assert [(ref.project, ref.session, ref.scan, ref.experiment) for ref in converter.launched] == [
        ("XA", "ACC-E1", "3", "E1")
    ]


def test_the_archive_path_is_the_session_label_and_the_resource_the_experiment_id(xnat: FakeXnat) -> None:
    """Both identifiers travel on this path and they are not the same thing. The procedure
    mounts <project>/arc001/<LABEL>; the REST registration addresses the experiment ID.
    Swapping them mounts a path that does not exist, and the job then reports DONE having
    converted nothing."""
    converter = StubConverter([DONE])

    drive(["--launch"], converter)

    ref = converter.launched[0]
    assert ref.session == "ACC-E1"
    assert ref.experiment == "E1"


def test_a_finished_conversion_is_registered_in_xnat_by_this_script(xnat: FakeXnat) -> None:
    """There is no Container Service upload step on SPCS: the job writes to a shared stage
    and nothing puts the result in the archive unless this does."""
    converter = StubConverter([DONE])

    code, lines = drive(["--launch"], converter)

    assert converter.collected == converter.launched
    assert any("2 file(s) registered in XNAT" in line for line in lines)
    assert code == 0


def test_a_registration_failure_is_not_counted_as_converted(xnat: FakeXnat) -> None:
    """The scan keeps its DICOM and no NIfTI, which is the state a later run selects again.
    Counting it as converted would mean the NIfTI is never produced and never retried."""
    converter = StubConverter([DONE], collect=ConversionError("XNAT refused the upload"))

    code, lines = drive(["--launch"], converter)

    assert code == 1
    assert any("registering it failed" in line for line in lines)


def test_a_failed_job_ends_the_scan_without_registering_anything(xnat: FakeXnat) -> None:
    converter = StubConverter([FAILED])

    code, lines = drive(["--launch"], converter)

    assert converter.collected == []
    assert code == 1
    assert any("the conversion job failed" in line for line in lines)


def test_a_conversion_still_running_when_the_wait_ends_exits_three(xnat: FakeXnat) -> None:
    """Exit 3 is "not finished", distinct from 1 "something failed". An operator re-runs on
    3 and investigates on 1."""
    converter = StubConverter([RUNNING] * 20)

    code, _ = drive(["--launch", "--wait", "1"], converter)

    assert code == 3
    assert converter.collected == []


def test_a_refused_scan_is_reported_and_not_followed(xnat: FakeXnat) -> None:
    converter = StubConverter([])
    converter.refuse = {"3"}

    code, lines = drive(["--launch"], converter)

    assert code == 1
    assert any("the converter refused" in line for line in lines)
    assert converter.launched == []


def test_the_container_service_routes_are_never_touched(xnat: FakeXnat) -> None:
    """The wrapper lookup, the container listing and the bulk launch are Container
    Service's alone. On SPCS the plugin is not even installed, so each of those calls is a
    404 that would be reported as "this project could not be read"."""
    converter = StubConverter([DONE])

    drive(["--launch"], converter)

    for _, url in xnat.calls:
        assert "/containers" not in url
        assert "/bulklaunch" not in url
        assert "/xapi/commands" not in url
