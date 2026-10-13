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

"""dcm2niix registration and project-trigger repair, on both deployment shapes (FLIP#1451).

Configuring an XNAT used to DELETE the dcm2niix command and register it again. XNAT gives the new
registration new ids, and every project's DICOM->NIfTI trigger names the wrapper by id, so each
configure silently disabled conversion for every project that already existed. On Compose the
"legacy site-wide" cleanup then deleted every project's subscription outright, through a selector on
a field no subscription has.

Both copies of the logic run here for real: ``configure-dcm2niix.sh`` (Compose) and the
``configure-dcm2niix`` container script of the chart's xnat-init job, taken from the template text.
They run against :mod:`fake_xnat`, a miniature XNAT behind a ``curl`` on ``PATH`` that keeps the
commands, wrapper enablement and subscriptions in a state file and validates subscriptions the way
container-service 3.8.1 does, so the assertions are about what XNAT ends up holding.

The chart's script runs under bash. In the cluster it runs under busybox ash, which these tests do
not have; dash, the other common /bin/sh, has no ``set -o pipefail`` and cannot run it at all.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

import fake_xnat

TESTS_DIR = Path(__file__).resolve().parent
XNAT_DIR = TESTS_DIR.parent
REPO_ROOT = XNAT_DIR.parents[1]
CONFIG_DIR = XNAT_DIR / "xnat" / "config"
COMPOSE_SCRIPT = CONFIG_DIR / "configure-dcm2niix.sh"
COMMAND_FILE = CONFIG_DIR / "dcm2niix_command.json"
INIT_JOB = REPO_ROOT / "trust" / "deploy" / "helm" / "templates" / "xnat-init-job.yaml"
IMAGING_API_PROJECTS = REPO_ROOT / "trust" / "imaging-api" / "imaging_api" / "services" / "projects.py"
RESTORE_CLI = REPO_ROOT / "trust" / "imaging-api" / "imaging_api" / "scripts" / "restore_nifti_triggers.py"
FAKE_XNAT = TESTS_DIR / "fake_xnat.py"

TIMEOUT_SECONDS = 120
SHAPES = ["compose", "chart"]

PROVIDER = fake_xnat.ACTION_KEY_PREFIX
TRIGGER = "DICOM-NIfTI Conversion"
WRAPPER = "dcm2niix-scan"
OLD_IMAGE = "xnat/dcm2niix:latest"
HUB_P = "0b9a4c3e-1d2f-4a5b-8c6d-7e8f9a0b1c2d"
HUB_Q = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
HUB_R = "2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f6a"


# --------------------------------------------------------------------------------------------------
# The two scripts
# --------------------------------------------------------------------------------------------------


def _block_scalar(lines: list[str], start: int) -> str:
    """The YAML block scalar whose content begins on ``lines[start]``, dedented."""
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = []
    for line in lines[start:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:] if line.strip() else "")
    return "\n".join(body).rstrip() + "\n"


def chart_script() -> str:
    """The configure-dcm2niix container's shell, exactly as the template carries it.

    Read from the template text rather than ``helm template`` (as test_chart_template_invariants
    does): the script block has no Helm templating, and this suite runs without helm.
    """
    lines = INIT_JOB.read_text().splitlines()
    container = next(i for i, line in enumerate(lines) if line.strip() == "- name: configure-dcm2niix")
    block = next(i for i in range(container, len(lines)) if lines[i].strip() == "- |")
    return _block_scalar(lines, block + 1)


def chart_command_json() -> str:
    """The chart ConfigMap's copy of dcm2niix_command.json."""
    lines = INIT_JOB.read_text().splitlines()
    key = next(i for i, line in enumerate(lines) if line.strip() == "dcm2niix_command.json: |")
    return _block_scalar(lines, key + 1)


def pinned_image() -> str:
    return str(json.loads(COMMAND_FILE.read_text())["image"])


# --------------------------------------------------------------------------------------------------
# A miniature XNAT
# --------------------------------------------------------------------------------------------------


def command(command_id: int, wrapper_id: int, image: str = OLD_IMAGE, name: str = "dcm2niix") -> dict[str, Any]:
    body = json.loads(COMMAND_FILE.read_text())
    body.update(id=command_id, name=name, image=image)
    body["xnat"][0]["id"] = wrapper_id
    return body


def subscription(
    sub_id: int,
    wrapper_id: int,
    projects: list[str],
    *,
    name: str = TRIGGER,
    active: bool = True,
    provider: str = PROVIDER,
    attributes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A subscription shaped as GET /xapi/events/subscriptions returns one: projects only in the filter."""
    return {
        "id": sub_id,
        "name": name,
        "active": active,
        "action-key": f"{provider}{wrapper_id}",
        "attributes": attributes or {},
        "event-filter": {
            "event-type": "org.nrg.xnat.eventservice.events.ScanEvent",
            "project-ids": projects,
            "status": "CREATED",
            "payload-filter": '(@.resources.length() > 0 && "DICOM" in @.resources[*].label)',
        },
        "act-as-event-user": False,
        "subscription-owner": "flipServiceAccount",
    }


def xnat_state(
    commands: list[dict[str, Any]],
    *,
    subscriptions: list[dict[str, Any]] | None = None,
    site_enabled: list[int] | None = None,
    project_enabled: dict[str, list[int]] | None = None,
    projects: dict[str, str] | None = None,
    faults: dict[str, bool] | None = None,
) -> dict[str, Any]:
    ids = [c["id"] for c in commands]
    wrapper_ids = [w["id"] for c in commands for w in c.get("xnat", [])]
    return {
        "commands": commands,
        "last_command_id": max(ids, default=0),
        "last_wrapper_id": max(wrapper_ids, default=0),
        "site_enabled": site_enabled if site_enabled is not None else wrapper_ids,
        "project_enabled": project_enabled or {},
        "projects": projects if projects is not None else {"P": HUB_P, "Q": HUB_Q},
        "subscriptions": subscriptions or [],
        "events_enabled": True,
        "hubs": [],
        "faults": faults or {},
    }


class Run:
    """One configure run: its exit code, output, the requests it made and the XNAT it left."""

    def __init__(self, result: subprocess.CompletedProcess[str], state: Path, log: Path) -> None:
        self.code = result.returncode
        self.output = result.stdout + result.stderr
        self.state: dict[str, Any] = json.loads(state.read_text())
        self.requests: list[dict[str, Any]] = [json.loads(line) for line in log.read_text().splitlines()]

    def made(self, method: str, path_pattern: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["method"] == method and re.fullmatch(path_pattern, r["path"])]

    def subscription(self, sub_id: int) -> dict[str, Any]:
        return next(s for s in self.state["subscriptions"] if s["id"] == sub_id)

    def dcm2niix(self) -> dict[str, Any]:
        (found,) = [c for c in self.state["commands"] if c["name"] == "dcm2niix"]
        return found


def is_valid(state: dict[str, Any], sub: dict[str, Any]) -> bool:
    """XNAT's validity rule, as fake_xnat applies it on every read."""
    return fake_xnat.validation_error(state, sub) is None


def configure(tmp_path: Path, shape: str, state: dict[str, Any]) -> Run:
    """Runs one shape's configure script against a fake XNAT holding ``state``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "curl").write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{FAKE_XNAT}" "$@"\n')
    (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "apk").write_text("#!/bin/sh\nexit 0\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    state_file = tmp_path / "xnat-state.json"
    state_file.write_text(json.dumps(state))
    log = tmp_path / "requests.log"
    log.touch()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_XNAT_STATE": str(state_file),
        "FAKE_XNAT_LOG": str(log),
        "XNAT_ADMIN_USER": "admin",
        "XNAT_ADMIN_PASSWORD": "pw",  # pragma: allowlist secret
        "XNAT_ADMIN_PASS": "pw",  # pragma: allowlist secret
        "DATA_PATH": "/opt/flip/xnat",
    }
    if shape == "compose":
        argv, cwd = ["bash", str(COMPOSE_SCRIPT)], CONFIG_DIR
    else:
        # The ConfigMap mount and the scratch files move under tmp_path; nothing else changes.
        cs_config = tmp_path / "cs-config"
        cs_config.mkdir()
        (cs_config / "dcm2niix_command.json").write_text(chart_command_json())
        (cs_config / "container-service-backend-configuration.kubernetes.json").write_text("{}")
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        script = chart_script().replace("/cs-config/", f"{cs_config}/").replace("/tmp/", f"{scratch}/")
        script_file = tmp_path / "configure-dcm2niix-k8s.sh"
        script_file.write_text(script)
        argv, cwd = ["bash", str(script_file)], tmp_path
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    return Run(result, state_file, log)


# --------------------------------------------------------------------------------------------------
# Updating in place
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("shape", SHAPES)
def test_an_existing_command_is_updated_in_place_and_never_deleted(tmp_path: Path, shape: str) -> None:
    state = xnat_state(
        [command(4, 4)],
        subscriptions=[subscription(1, 4, ["P"])],
        project_enabled={"P": [4]},
    )
    run = configure(tmp_path, shape, state)

    assert run.code == 0, run.output
    assert not run.made("DELETE", r"/xapi/commands.*"), "the configure step deleted a command"
    assert not run.made("POST", r"/xapi/commands"), "registered a second command instead of updating"
    (update,) = run.made("POST", r"/xapi/commands/4")
    sent = chart_command_json() if shape == "chart" else COMMAND_FILE.read_text()
    assert json.loads(update["body"]) == json.loads(sent), "the update must carry the whole command definition"

    registered = run.dcm2niix()
    assert registered["id"] == 4
    assert (registered["xnat"][0]["id"], registered["xnat"][0]["name"]) == (4, WRAPPER)
    assert registered["image"] == pinned_image(), "the image pin did not reach the registered command"
    # The project's trigger still names a real, enabled wrapper, so it never went invalid.
    assert run.subscription(1)["action-key"] == f"{PROVIDER}4"
    assert is_valid(run.state, run.subscription(1))
    assert not run.made("PUT", r"/xapi/events/subscription/\d+"), "a valid trigger was rewritten"


@pytest.mark.parametrize("shape", SHAPES)
def test_a_command_is_registered_only_when_none_exists(tmp_path: Path, shape: str) -> None:
    state = xnat_state([])
    state["last_command_id"], state["last_wrapper_id"] = 6, 8
    run = configure(tmp_path, shape, state)

    assert run.code == 0, run.output
    assert len(run.made("POST", r"/xapi/commands")) == 1
    assert not run.made("POST", r"/xapi/commands/\d+")
    registered = run.dcm2niix()
    assert (registered["id"], registered["xnat"][0]["id"]) == (7, 9)
    assert registered["image"] == pinned_image()
    assert 9 in run.state["site_enabled"]


@pytest.mark.parametrize("shape", SHAPES)
def test_a_second_run_changes_nothing_about_the_triggers(tmp_path: Path, shape: str) -> None:
    """Upgrading twice must leave the ids alone the second time too."""
    first = configure(tmp_path / "one", shape, xnat_state([command(4, 4)], subscriptions=[subscription(1, 4, ["P"])]))
    assert first.code == 0, first.output
    second = configure(tmp_path / "two", shape, first.state)

    assert second.code == 0, second.output
    assert second.dcm2niix()["id"] == 4
    assert second.dcm2niix()["xnat"][0]["id"] == 4
    assert not second.made("PUT", r"/xapi/events/subscription/\d+")


@pytest.mark.parametrize("shape", SHAPES)
def test_refuses_to_pick_between_two_dcm2niix_commands(tmp_path: Path, shape: str) -> None:
    second = command(5, 5)
    second["version"] = "9.9"
    run = configure(tmp_path, shape, xnat_state([command(4, 4), second]))

    assert run.code != 0
    assert "expected at most one" in run.output
    assert not run.made("POST", r"/xapi/commands(/\d+)?")
    assert not run.made("DELETE", r"/xapi/commands.*")


@pytest.mark.parametrize("shape", SHAPES)
def test_fails_when_the_pinned_image_does_not_reach_the_command(tmp_path: Path, shape: str) -> None:
    """The check behind "an image pin change still reaches the registered command"."""
    run = configure(tmp_path, shape, xnat_state([command(4, 4)], faults={"drop_image_on_update": True}))

    assert run.code != 0
    assert OLD_IMAGE in run.output
    assert "not the pinned" in run.output


# --------------------------------------------------------------------------------------------------
# Repairing triggers an earlier configure broke
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("shape", SHAPES)
def test_triggers_left_on_a_deleted_wrapper_are_repointed(tmp_path: Path, shape: str) -> None:
    """AZ1 after the 13 Oct upgrade: wrapper 4 deleted, dcm2niix re-registered as 5."""
    state = xnat_state(
        [command(5, 5, image=pinned_image())],
        subscriptions=[
            subscription(1, 4, ["P"]),
            subscription(2, 5, ["Q"]),
            subscription(3, 4, ["R"], active=False, attributes={"note": "kept"}),
        ],
        # P and R were enabled for the deleted wrapper only; Q was created after the re-register.
        project_enabled={"P": [4], "Q": [5], "R": [4]},
        projects={"P": HUB_P, "Q": HUB_Q, "R": HUB_R},
    )
    assert not is_valid(state, state["subscriptions"][0]), "fixture: the stale trigger must start invalid"
    run = configure(tmp_path, shape, state)

    assert run.code == 0, run.output
    for sub_id in (1, 2, 3):
        assert run.subscription(sub_id)["action-key"] == f"{PROVIDER}5"
        assert is_valid(run.state, run.subscription(sub_id)), f"subscription {sub_id} is still invalid"
    assert run.subscription(3)["active"] is False, "an inactive trigger was switched on"
    assert run.subscription(3)["attributes"] == {"note": "kept"}
    assert run.subscription(3)["act-as-event-user"] is False
    assert {r["path"] for r in run.made("PUT", r"/xapi/events/subscription/\d+")} == {
        "/xapi/events/subscription/1",
        "/xapi/events/subscription/3",
    }, "only the stale triggers may be rewritten"
    for project in ("P", "R"):
        assert f"Repaired the DICOM->NIfTI trigger of project {project}" in run.output
    assert "WARNING: invalid DICOM->NIfTI trigger" not in run.output


@pytest.mark.parametrize("shape", SHAPES)
def test_the_project_is_enabled_before_its_trigger_is_updated(tmp_path: Path, shape: str) -> None:
    """XNAT validates on update, and the project's old enablement belongs to the deleted wrapper."""
    state = xnat_state(
        [command(5, 5, image=pinned_image())], subscriptions=[subscription(1, 4, ["P"])], project_enabled={"P": [4]}
    )
    run = configure(tmp_path, shape, state)

    assert run.code == 0, run.output
    order = [r["path"] for r in run.requests if r["method"] == "PUT"]
    enable = order.index("/xapi/projects/P/commands/5/wrappers/dcm2niix-scan/enabled")
    assert enable < order.index("/xapi/events/subscription/1")


@pytest.mark.parametrize("shape", SHAPES)
def test_the_repair_names_the_wrapper_id_not_the_command_id(tmp_path: Path, shape: str) -> None:
    """imaging-api used to write the command id, which only worked while the two were equal."""
    state = xnat_state(
        [command(9, 12, image=pinned_image())],
        subscriptions=[subscription(1, 9, ["P"])],
        project_enabled={"P": [9]},
    )
    run = configure(tmp_path, shape, state)

    assert run.code == 0, run.output
    assert run.subscription(1)["action-key"] == f"{PROVIDER}12"
    assert is_valid(run.state, run.subscription(1))


@pytest.mark.parametrize("shape", SHAPES)
def test_other_subscriptions_are_left_alone(tmp_path: Path, shape: str) -> None:
    state = xnat_state(
        [command(5, 5, image=pinned_image())],
        subscriptions=[
            subscription(1, 4, ["P"], name="Someone else's trigger"),
            subscription(2, 4, ["P"], provider="org.nrg.xnat.eventservice.actions.EventServiceLoggingAction:"),
        ],
    )
    before = copy.deepcopy(state["subscriptions"])
    run = configure(tmp_path, shape, state)

    assert run.code == 0, run.output
    assert not run.made("PUT", r"/xapi/events/subscription/\d+")
    assert run.state["subscriptions"] == before


@pytest.mark.parametrize("shape", SHAPES)
def test_a_trigger_whose_project_is_gone_is_skipped_not_fatal(tmp_path: Path, shape: str) -> None:
    """One deleted project must not wedge every later deploy."""
    state = xnat_state(
        [command(5, 5, image=pinned_image())],
        subscriptions=[subscription(1, 4, ["GONE"]), subscription(2, 4, ["P"])],
        project_enabled={"P": [4]},
    )
    run = configure(tmp_path, shape, state)

    assert run.code == 0, run.output
    assert "Skipped subscription 1" in run.output
    assert run.subscription(1)["action-key"] == f"{PROVIDER}4"
    assert run.subscription(2)["action-key"] == f"{PROVIDER}5"
    # Still invalid, and said so.
    assert "WARNING: invalid DICOM->NIfTI trigger: subscription 1" in run.output


@pytest.mark.parametrize("shape", SHAPES)
def test_flip_projects_with_no_trigger_are_named(tmp_path: Path, shape: str) -> None:
    """A Compose site upgraded before the fix lost every trigger; the deploy log must say which."""
    state = xnat_state(
        [command(7, 7, image=pinned_image())],
        projects={"P": HUB_P, "Q": HUB_Q, "OPERATOR": "not-a-hub-id"},
        subscriptions=[subscription(1, 7, ["Q"])],
        project_enabled={"Q": [7]},
    )
    run = configure(tmp_path, shape, state)

    assert run.code == 0, run.output
    assert f"WARNING: project P (hub project {HUB_P}) has no DICOM->NIfTI trigger" in run.output
    assert "WARNING: project Q " not in run.output
    assert "OPERATOR" not in run.output, "a project FLIP did not create was reported"
    assert not run.made("POST", r"/xapi/(projects/[^/]+/)?events/subscription"), "the configure step created a trigger"


# --------------------------------------------------------------------------------------------------
# The Compose cleanup
# --------------------------------------------------------------------------------------------------


def test_compose_cleanup_deletes_only_the_legacy_site_wide_subscription(tmp_path: Path) -> None:
    """It selected on `project-id`, which no subscription has, so it deleted every project's trigger."""
    state = xnat_state(
        [command(4, 4)],
        subscriptions=[
            subscription(1, 4, ["P"]),
            subscription(2, 4, [], name="DICOM-NifTi Conversion"),
            subscription(3, 4, [], name="An operator's site-wide subscription"),
        ],
        project_enabled={"P": [4]},
    )
    run = configure(tmp_path, "compose", state)

    assert run.code == 0, run.output
    assert [r["path"] for r in run.made("DELETE", r"/xapi/events/subscription/\d+")] == ["/xapi/events/subscription/2"]
    assert {s["id"] for s in run.state["subscriptions"]} == {1, 3}


# --------------------------------------------------------------------------------------------------
# Static guards
# --------------------------------------------------------------------------------------------------

DELETE_COMMAND = re.compile(r"DELETE[^\n]*/xapi/commands|/xapi/commands[^\n]*DELETE")


@pytest.mark.parametrize("shape", SHAPES)
def test_no_configure_script_deletes_a_command(shape: str) -> None:
    """Belt and braces for paths the runs above do not reach."""
    text = COMPOSE_SCRIPT.read_text() if shape == "compose" else chart_script()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "/xapi/commands" in code, "guard the guard: the script no longer talks to the command API"
    assert not DELETE_COMMAND.search(code), "a configure script deletes a dcm2niix command again (FLIP#1451)"


def test_the_trigger_name_is_the_one_imaging_api_creates() -> None:
    """The repair finds FLIP's subscriptions by name, so all three copies must agree."""
    compose = re.search(r'^DCM2NIIX_SUBSCRIPTION_NAME="([^"]+)"', COMPOSE_SCRIPT.read_text(), re.M)
    chart = re.search(r'^DCM2NIIX_SUBSCRIPTION_NAME="([^"]+)"', chart_script(), re.M)
    imaging = re.search(r'^DCM2NIIX_SUBSCRIPTION_NAME = "([^"]+)"', IMAGING_API_PROJECTS.read_text(), re.M)
    assert compose, "configure-dcm2niix.sh no longer sets DCM2NIIX_SUBSCRIPTION_NAME"
    assert chart, "the chart's configure-dcm2niix no longer sets DCM2NIIX_SUBSCRIPTION_NAME"
    assert imaging, "imaging-api no longer defines DCM2NIIX_SUBSCRIPTION_NAME"
    assert compose.group(1) == chart.group(1) == imaging.group(1) == TRIGGER


HUB_ID_PATTERN = "^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"


def test_flip_projects_are_recognised_the_same_way_everywhere() -> None:
    """Which XNAT projects are FLIP's (a hub-id secondary_ID) decides who gets reported and restored."""
    for name, text in (
        ("configure-dcm2niix.sh", COMPOSE_SCRIPT.read_text()),
        ("the chart's configure-dcm2niix", chart_script()),
        ("restore_nifti_triggers.py", RESTORE_CLI.read_text()),
    ):
        assert HUB_ID_PATTERN in text, f"{name} no longer recognises FLIP projects by {HUB_ID_PATTERN}"


def test_the_chart_script_extracts() -> None:
    """Guard the guard: the template parsing above still finds a real script and command."""
    script = chart_script()
    assert script.startswith("set -euo pipefail\n"), textwrap.shorten(script, 80)
    assert json.loads(chart_command_json())["image"] == pinned_image()
