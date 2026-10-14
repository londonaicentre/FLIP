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
"""``restore_nifti_triggers``: recreate deleted DICOM->NIfTI triggers, only as the operator says (FLIP#1451)."""

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from imaging_api.config import get_settings
from imaging_api.routers.schemas import Project
from imaging_api.scripts.restore_nifti_triggers import run
from imaging_api.services.projects import DCM2NIIX_SUBSCRIPTION_NAME

MODULE = "imaging_api.scripts.restore_nifti_triggers"
HUB_A = "0b9a4c3e-1d2f-4a5b-8c6d-7e8f9a0b1c2d"
HUB_B = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
HUB_C = "2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f6a"


def _project(xnat_id: str, hub_id: str, name: str) -> Project:
    return Project(
        ID=xnat_id, secondary_ID=hub_id, name=name, pi_firstname="", pi_lastname="", URI=f"/data/projects/{xnat_id}"
    )


PROJECTS = [
    _project("XA", HUB_A, "a"),
    _project("XB", HUB_B, "b"),
    _project("XC", HUB_C, "c"),
    _project("OPERATOR", "not-a-hub-id", "someone else's"),
]


def _trigger(project: str, active: bool = True, valid: bool = True) -> dict[str, Any]:
    return {
        "id": 7,
        "name": DCM2NIIX_SUBSCRIPTION_NAME,
        "active": active,
        "valid": valid,
        "validation-message": None if valid else "Could not validate Action",
        "action-key": "org.nrg.containers.services.CommandActionProvider:5",
        "event-filter": {"project-ids": [project]},
    }


def _listing(subscriptions: list[dict[str, Any]] | None) -> MagicMock:
    """GET /xapi/events/subscriptions answering with ``subscriptions`` (None: Event Service off)."""
    return MagicMock(status_code=200, json=MagicMock(return_value=subscriptions))


def _drive(
    argv: list[str],
    subscriptions: list[dict[str, Any]] | None = None,
    *,
    listing: MagicMock | None = None,
    create_side_effect: Callable[..., None] | None = None,
) -> tuple[int, list[str], MagicMock]:
    """Runs the command against a mocked XNAT; returns (exit code, report lines, create mock)."""
    lines: list[str] = []
    with (
        patch(f"{MODULE}.get_all_projects", return_value=PROJECTS),
        patch(f"{MODULE}.requests.get", return_value=listing or _listing(subscriptions or [])) as get,
        patch(f"{MODULE}.create_project_event_subscription", side_effect=create_side_effect) as create,
    ):
        code = run(argv, {"Cookie": "x"}, out=lines.append)
    assert get.call_args[0][0].endswith("/xapi/events/subscriptions")
    return code, lines, create


def test_without_instructions_it_only_reports():
    code, lines, create = _drive([], [_trigger("XA")])

    assert code == 0
    create.assert_not_called()
    report = "\n".join(lines)
    assert f"XA (hub project {HUB_A}, 'a'): has a trigger (subscription 7 active)." in report
    assert f"XB (hub project {HUB_B}, 'b'): NO trigger, not restored" in report
    assert "OPERATOR" not in report, "a project FLIP did not create was reported"


def test_restores_exactly_as_the_operator_says_by_hub_or_xnat_id():
    code, lines, create = _drive(["--active", HUB_B, "--inactive", "XC"], [_trigger("XA")])

    assert code == 0
    image = get_settings().DCM2NIIX_IMAGE
    assert sorted(c.args[:3] for c in create.call_args_list) == [("XB", image, True), ("XC", image, False)]
    assert any("XB" in line and "restored it active" in line for line in lines)
    assert any("XC" in line and "restored it inactive" in line for line in lines)


def test_rest_covers_the_unnamed_ones_and_an_explicit_name_beats_it():
    code, _, create = _drive(["--rest", "active", "--inactive", HUB_C])

    assert code == 0
    calls = {c.args[0]: c.args[2] for c in create.call_args_list}
    assert calls == {"XA": True, "XB": True, "XC": False}


def test_a_project_that_has_a_trigger_is_never_touched_even_when_named():
    """Re-pointing a stale trigger is the configure step's job; a second one would double-convert."""
    code, lines, create = _drive(["--active", "XA", "--rest", "active"], [_trigger("XA", valid=False)])

    assert code == 0
    assert "XA" not in [c.args[0] for c in create.call_args_list]
    assert any("XA" in line and "INVALID" in line and "left as it is" in line for line in lines)


def test_another_subscription_on_the_project_is_not_a_trigger():
    other = {**_trigger("XA"), "name": "Someone else's subscription"}
    code, _, create = _drive(["--active", "XA"], [other])

    assert code == 0
    assert [c.args[0] for c in create.call_args_list] == ["XA"]


def test_dry_run_changes_nothing():
    code, lines, create = _drive(["--rest", "inactive", "--dry-run"])

    assert code == 0
    create.assert_not_called()
    assert sum("would restore it inactive" in line for line in lines) == 3


@pytest.mark.parametrize(
    "argv",
    [["--active", "no-such-project"], ["--active", "XB", "--inactive", HUB_B]],
    ids=["unknown-id", "both-active-and-inactive"],
)
def test_a_bad_request_changes_nothing(argv):
    lines: list[str] = []
    with (
        patch(f"{MODULE}.get_all_projects", return_value=PROJECTS),
        patch(f"{MODULE}.requests.get") as get,
        patch(f"{MODULE}.create_project_event_subscription") as create,
    ):
        code = run(argv, {}, out=lines.append)

    assert code == 2
    get.assert_not_called()
    create.assert_not_called()
    assert "Nothing was changed" in lines[-1]


def test_one_failure_does_not_strand_the_rest():
    def create(project_id: str, *_: Any) -> None:
        if project_id == "XA":
            raise Exception("XNAT said no")

    code, lines, _ = _drive(["--rest", "active"], create_side_effect=create)

    assert code == 1
    assert any("XA" in line and "FAILED: XNAT said no" in line for line in lines)
    assert any("XB" in line and "restored it active" in line for line in lines)
    assert "Restored: 2. Failed: 1." in lines[-1]


TOMCAT_403 = "<html><head><title>HTTP Status 403</title><style>" + "x" * 2000 + "</style></head></html>"


@pytest.mark.parametrize(
    ("listing", "refusal"),
    [
        (MagicMock(status_code=403, text=TOMCAT_403), "event subscriptions failed: 403"),
        # XNAT lists null while the Event Service is off: read as "no triggers", --rest would duplicate them all.
        (_listing(None), "Event Service is off"),
    ],
    ids=["refused", "event-service-off"],
)
def test_a_listing_it_cannot_read_stops_before_any_change(listing: MagicMock, refusal: str):
    with (
        patch(f"{MODULE}.get_all_projects", return_value=PROJECTS),
        patch(f"{MODULE}.requests.get", return_value=listing),
        patch(f"{MODULE}.create_project_event_subscription") as create,
        pytest.raises(Exception, match=refusal) as excinfo,
    ):
        run(["--rest", "active"], {}, out=lambda _: None)

    create.assert_not_called()
    assert len(str(excinfo.value)) < 400, "the operator's terminal got the whole Tomcat page"
