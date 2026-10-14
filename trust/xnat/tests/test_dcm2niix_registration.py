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

"""dcm2niix registration and project-trigger repair, on both Container Service backends (FLIP#1451).

Configuring an XNAT used to delete the dcm2niix command and register it again, which gave its wrapper
a new id and so invalidated every project's DICOM->NIfTI trigger. Each test runs
``configure-dcm2niix.sh`` for real against :mod:`fake_xnat`, a miniature XNAT behind a ``curl`` on
``PATH``, and asserts what XNAT ends up holding.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import fake_xnat

TESTS_DIR = Path(__file__).resolve().parent
XNAT_DIR = TESTS_DIR.parent
REPO_ROOT = XNAT_DIR.parents[1]
CONFIG_DIR = XNAT_DIR / "xnat" / "config"
SCRIPT = CONFIG_DIR / "configure-dcm2niix.sh"
COMMAND_FILE = CONFIG_DIR / "dcm2niix_command.json"
IMAGING_API_PROJECTS = REPO_ROOT / "trust" / "imaging-api" / "imaging_api" / "services" / "projects.py"
RESTORE_CLI = REPO_ROOT / "trust" / "imaging-api" / "imaging_api" / "scripts" / "restore_nifti_triggers.py"
FAKE_XNAT = TESTS_DIR / "fake_xnat.py"

TIMEOUT_SECONDS = 120
BACKENDS = ["docker", "kubernetes"]
DATA_PATH = "/opt/flip/xnat"
PVC = "trust-release-flip-trust-xnat-web"
RESOURCE_KEYS = ("reserve-memory", "limit-memory", "limit-cpu")

PROVIDER = fake_xnat.ACTION_KEY_PREFIX
TRIGGER = "DICOM-NIfTI Conversion"
WRAPPER = "dcm2niix-scan"
OLD_IMAGE = "xnat/dcm2niix:latest"
HUB_P = "0b9a4c3e-1d2f-4a5b-8c6d-7e8f9a0b1c2d"
HUB_Q = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
HUB_R = "2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f6a"


def command_file() -> dict[str, Any]:
    return dict(json.loads(COMMAND_FILE.read_text()))


def pinned_image() -> str:
    return str(command_file()["image"])


def expected_body(backend: str) -> dict[str, Any]:
    """The command definition each backend sends: Kubernetes without the resource keys (FLIP#1448)."""
    body = command_file()
    if backend == "kubernetes":
        for key in RESOURCE_KEYS:
            body.pop(key, None)
    return body


# --------------------------------------------------------------------------------------------------
# A miniature XNAT
# --------------------------------------------------------------------------------------------------


def command(command_id: int, wrapper_id: int, image: str = OLD_IMAGE, name: str = "dcm2niix") -> dict[str, Any]:
    body = command_file()
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
    attributes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A subscription shaped as GET /xapi/events/subscriptions returns one: projects only in the filter."""
    return {
        "id": sub_id,
        "name": name,
        "active": active,
        "action-key": f"{PROVIDER}{wrapper_id}",
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
    faults: dict[str, Any] | None = None,
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


def configure(tmp_path: Path, state: dict[str, Any], backend: str = "docker", **env_overrides: str) -> Run:
    """Runs configure-dcm2niix.sh against a fake XNAT holding ``state``, with the environment ``backend``'s
    deployment gives it: Compose's xnat-web carries DATA_PATH, the chart sets XNAT_CS_BACKEND and XNAT_CS_PVC_NAME.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "curl").write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{FAKE_XNAT}" "$@"\n')
    (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    state_file = tmp_path / "xnat-state.json"
    state_file.write_text(json.dumps(state))
    log = tmp_path / "requests.log"
    log.touch()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("DATA_PATH", "XNAT_CS_BACKEND", "XNAT_CS_PVC_NAME", "XNAT_URL")
    }
    env.update(
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        FAKE_XNAT_STATE=str(state_file),
        FAKE_XNAT_LOG=str(log),
        XNAT_ADMIN_USER="admin",
        XNAT_ADMIN_PASSWORD="pw",  # pragma: allowlist secret
    )
    if backend == "docker":
        env["DATA_PATH"] = DATA_PATH
    else:
        env.update(XNAT_CS_BACKEND="kubernetes", XNAT_CS_PVC_NAME=PVC, XNAT_URL="http://xnat-web:8080")
    env.update(env_overrides)
    result = subprocess.run(
        ["bash", str(SCRIPT)], cwd=CONFIG_DIR, env=env, capture_output=True, text=True, timeout=TIMEOUT_SECONDS
    )
    return Run(result, state_file, log)


# --------------------------------------------------------------------------------------------------
# The backend
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_each_backend_registers_its_own_container_service_backend_and_pings_it(tmp_path: Path, backend: str) -> None:
    run = configure(tmp_path, xnat_state([command(4, 4)]), backend)

    assert run.code == 0, run.output
    registered = run.state["backend"]
    if backend == "docker":
        assert registered["host"] == "tcp://xnat-socket-proxy:2375"
        assert registered["path-translation-docker-prefix"] == DATA_PATH
    else:
        assert registered["backend"] == "kubernetes"
        assert registered["combined-pvc-name"] == PVC
        assert registered["combined-path-translation"] == "/data/xnat/", "the trailing slash is load-bearing"
    assert run.made("GET", r"/xapi/docker/server/ping"), "the backend was never pinged"


@pytest.mark.parametrize("backend", BACKENDS)
def test_only_the_docker_backend_registers_an_image_host(tmp_path: Path, backend: str) -> None:
    """Swarm launches NPE without one; kubelet pulls on Kubernetes, so none is needed there."""
    run = configure(tmp_path, xnat_state([command(4, 4)]), backend)

    assert run.code == 0, run.output
    hosts = [h["url"] for h in run.state["hubs"]]
    assert hosts == (["ghcr.io"] if backend == "docker" else [])


@pytest.mark.parametrize(
    ("backend", "missing"), [("docker", "DATA_PATH"), ("kubernetes", "XNAT_CS_PVC_NAME")], ids=["docker", "kubernetes"]
)
def test_each_backend_refuses_to_run_without_its_setting(tmp_path: Path, backend: str, missing: str) -> None:
    run = configure(tmp_path, xnat_state([command(4, 4)]), backend, **{missing: ""})

    assert run.code != 0
    assert missing in run.output
    assert not run.requests, "it talked to XNAT before checking its own settings"


def test_an_unknown_backend_is_refused(tmp_path: Path) -> None:
    run = configure(tmp_path, xnat_state([command(4, 4)]), XNAT_CS_BACKEND="swarm")

    assert run.code != 0
    assert "XNAT_CS_BACKEND must be docker or kubernetes" in run.output
    assert not run.requests


def test_a_missing_container_manager_role_is_named(tmp_path: Path) -> None:
    run = configure(tmp_path, xnat_state([command(4, 4)], faults={"no_container_manager": True}))

    assert run.code != 0
    assert "does not hold the ContainerManager role" in run.output
    assert not run.made("POST", r"/xapi/.*")


# --------------------------------------------------------------------------------------------------
# Updating in place
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_an_existing_command_is_updated_in_place_and_never_deleted(tmp_path: Path, backend: str) -> None:
    state = xnat_state([command(4, 4)], subscriptions=[subscription(1, 4, ["P"])], project_enabled={"P": [4]})
    run = configure(tmp_path, state, backend)

    assert run.code == 0, run.output
    assert not run.made("DELETE", r"/xapi/commands.*"), "the configure step deleted a command"
    assert not run.made("POST", r"/xapi/commands"), "registered a second command instead of updating"
    (update,) = run.made("POST", r"/xapi/commands/4")
    assert json.loads(update["body"]) == expected_body(backend), "the update must carry the whole command definition"

    registered = run.dcm2niix()
    assert registered["id"] == 4
    assert (registered["xnat"][0]["id"], registered["xnat"][0]["name"]) == (4, WRAPPER)
    assert registered["image"] == pinned_image(), "the image pin did not reach the registered command"
    # The project's trigger still names a real, enabled wrapper, so it never went invalid.
    assert run.subscription(1)["action-key"] == f"{PROVIDER}4"
    assert is_valid(run.state, run.subscription(1))
    assert not run.made("PUT", r"/xapi/events/subscription/\d+"), "a valid trigger was rewritten"


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_command_is_registered_only_when_none_exists(tmp_path: Path, backend: str) -> None:
    state = xnat_state([])
    state["last_command_id"], state["last_wrapper_id"] = 6, 8
    run = configure(tmp_path, state, backend)

    assert run.code == 0, run.output
    (register,) = run.made("POST", r"/xapi/commands")
    assert json.loads(register["body"]) == expected_body(backend)
    assert not run.made("POST", r"/xapi/commands/\d+")
    registered = run.dcm2niix()
    assert (registered["id"], registered["xnat"][0]["id"]) == (7, 9)
    assert registered["image"] == pinned_image()
    assert 9 in run.state["site_enabled"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_kubernetes_moves_the_command_off_its_resource_settings_and_docker_keeps_them(
    tmp_path: Path, backend: str
) -> None:
    """On Kubernetes the keys made every pod reserve a whole CPU (FLIP#1448); on Docker they are limits."""
    run = configure(tmp_path, xnat_state([command(4, 4)]), backend)

    assert run.code == 0, run.output
    in_file = {key for key in RESOURCE_KEYS if key in command_file()}
    assert in_file, "fixture: dcm2niix_command.json carries no resource keys to strip"
    kept = {key for key in RESOURCE_KEYS if key in run.dcm2niix()}
    assert kept == (set() if backend == "kubernetes" else in_file)


def test_a_second_run_changes_nothing_about_the_triggers(tmp_path: Path) -> None:
    first = configure(tmp_path / "one", xnat_state([command(4, 4)], subscriptions=[subscription(1, 4, ["P"])]))
    assert first.code == 0, first.output
    second = configure(tmp_path / "two", first.state)

    assert second.code == 0, second.output
    assert (second.dcm2niix()["id"], second.dcm2niix()["xnat"][0]["id"]) == (4, 4)
    assert not second.made("PUT", r"/xapi/events/subscription/\d+")


def test_refuses_to_pick_between_two_dcm2niix_commands(tmp_path: Path) -> None:
    second = command(5, 5)
    second["version"] = "9.9"
    run = configure(tmp_path, xnat_state([command(4, 4), second]))

    assert run.code != 0
    assert "expected at most one" in run.output
    assert "command 5 (wrapper 5)" in run.output, "the operator needs the wrapper ids to choose"
    assert not run.made("POST", r"/xapi/commands(/\d+)?")
    assert not run.made("DELETE", r"/xapi/commands.*")


def test_fails_when_the_pinned_image_does_not_reach_the_command(tmp_path: Path) -> None:
    run = configure(tmp_path, xnat_state([command(4, 4)], faults={"drop_image_on_update": True}))

    assert run.code != 0
    assert f"runs '{OLD_IMAGE}', not the pinned" in run.output


# --------------------------------------------------------------------------------------------------
# Repairing triggers an earlier configure broke
# --------------------------------------------------------------------------------------------------


def test_triggers_left_on_a_deleted_wrapper_are_repointed(tmp_path: Path) -> None:
    """Wrapper 4 deleted by an earlier configure, dcm2niix re-registered as 5."""
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
    run = configure(tmp_path, state)

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


def test_the_project_is_enabled_before_its_trigger_is_updated(tmp_path: Path) -> None:
    """XNAT validates on update, and the project's old enablement belongs to the deleted wrapper."""
    state = xnat_state(
        [command(5, 5, image=pinned_image())], subscriptions=[subscription(1, 4, ["P"])], project_enabled={"P": [4]}
    )
    run = configure(tmp_path, state)

    assert run.code == 0, run.output
    order = [r["path"] for r in run.requests if r["method"] == "PUT"]
    enable = order.index("/xapi/projects/P/commands/5/wrappers/dcm2niix-scan/enabled")
    assert enable < order.index("/xapi/events/subscription/1")


def test_the_repair_names_the_wrapper_id_not_the_command_id(tmp_path: Path) -> None:
    """imaging-api used to write the command id, which only worked while the two were equal."""
    state = xnat_state(
        [command(9, 12, image=pinned_image())], subscriptions=[subscription(1, 9, ["P"])], project_enabled={"P": [9]}
    )
    run = configure(tmp_path, state)

    assert run.code == 0, run.output
    assert run.subscription(1)["action-key"] == f"{PROVIDER}12"
    assert is_valid(run.state, run.subscription(1))


def test_a_flip_trigger_on_another_live_wrapper_is_left_alone(tmp_path: Path) -> None:
    """Only a trigger whose action XNAT no longer offers is FLIP's to repair; a live one is someone's choice."""
    other = command(6, 6, name="dcm2niix-gpu")
    other["xnat"][0]["name"] = "dcm2niix-gpu-scan"
    state = xnat_state(
        [command(5, 5, image=pinned_image()), other],
        subscriptions=[subscription(1, 6, ["P"])],
        project_enabled={"P": [6]},
    )
    before = copy.deepcopy(state["subscriptions"])
    run = configure(tmp_path, state)

    assert run.code == 0, run.output
    assert not run.made("PUT", r"/xapi/events/subscription/\d+")
    assert run.state["subscriptions"] == before


def test_other_subscriptions_are_left_alone(tmp_path: Path) -> None:
    """Another name is not FLIP's, and a site-wide one revived would convert every project's scans."""
    state = xnat_state(
        [command(5, 5, image=pinned_image())],
        subscriptions=[subscription(1, 4, ["P"], name="Someone else's trigger"), subscription(2, 4, [])],
    )
    before = copy.deepcopy(state["subscriptions"])
    run = configure(tmp_path, state)

    assert run.code == 0, run.output
    assert not run.made("PUT", r"/xapi/events/subscription/\d+")
    assert run.state["subscriptions"] == before


def test_a_trigger_whose_project_is_gone_is_skipped_not_fatal(tmp_path: Path) -> None:
    """One deleted project must not wedge every later deploy."""
    state = xnat_state(
        [command(5, 5, image=pinned_image())],
        subscriptions=[subscription(1, 4, ["GONE"]), subscription(2, 4, ["P"])],
        project_enabled={"P": [4]},
    )
    run = configure(tmp_path, state)

    assert run.code == 0, run.output
    assert "Skipped subscription 1" in run.output
    assert run.subscription(1)["action-key"] == f"{PROVIDER}4"
    assert run.subscription(2)["action-key"] == f"{PROVIDER}5"
    assert "WARNING: invalid DICOM->NIfTI trigger: subscription 1" in run.output, "still invalid, and said so"


def test_flip_projects_with_no_trigger_are_named(tmp_path: Path) -> None:
    """A Compose site upgraded before the fix lost every trigger; the deploy log must say which."""
    state = xnat_state(
        [command(7, 7, image=pinned_image())],
        projects={"P": HUB_P, "Q": HUB_Q, "OPERATOR": "not-a-hub-id"},
        subscriptions=[subscription(1, 7, ["Q"])],
        project_enabled={"Q": [7]},
    )
    run = configure(tmp_path, state)

    assert run.code == 0, run.output
    assert f"WARNING: project P (hub project {HUB_P}) has no DICOM->NIfTI trigger" in run.output
    assert "WARNING: project Q " not in run.output
    assert "OPERATOR" not in run.output, "a project FLIP did not create was reported"
    assert not run.made("POST", r"/xapi/(projects/[^/]+/)?events/subscription"), "the configure step created a trigger"


def test_the_script_deletes_no_subscription(tmp_path: Path) -> None:
    """The legacy cleanup selected on a field no subscription has, so it deleted every project's trigger."""
    state = xnat_state(
        [command(4, 4)],
        subscriptions=[
            subscription(1, 4, ["P"]),
            subscription(2, 4, [], name="DICOM-NifTi Conversion"),
            subscription(3, 4, [], name="An operator's site-wide subscription"),
        ],
        project_enabled={"P": [4]},
    )
    run = configure(tmp_path, state)

    assert run.code == 0, run.output
    assert not run.made("DELETE", r"/xapi/(projects/[^/]+/)?events/subscription/\d+")
    assert {s["id"] for s in run.state["subscriptions"]} == {1, 2, 3}


def test_listings_over_the_argument_limit_still_configure(tmp_path: Path) -> None:
    """Listings over 1 MiB, past Linux's 128 KiB per-argument and macOS's 1 MiB argv caps (a 3,500-project site)."""
    projects = {f"P{i:05d}": f"{i:08x}-1d2f-4a5b-8c6d-7e8f9a0b1c2d" for i in range(3500)}
    subscriptions = [subscription(i, 5, [pid]) for i, pid in enumerate(projects, start=100)]
    subscriptions[0]["action-key"] = f"{PROVIDER}4"
    state = xnat_state(
        [command(5, 5, image=pinned_image())],
        subscriptions=subscriptions,
        project_enabled={pid: [5] for pid in projects},
        projects=projects,
    )
    for path in ("/xapi/events/subscriptions", "/data/projects"):
        _, body = fake_xnat.handle(copy.deepcopy(state), fake_xnat.Request([f"http://xnat-web:8080{path}"]))
        assert len(body) > 1024 * 1024, f"fixture: {path} is only {len(body)} bytes"
    del state["subscriptions"][-1]  # one FLIP project left with no trigger, for the report
    run = configure(tmp_path, state)

    assert run.code == 0, run.output[-2000:]
    assert run.subscription(100)["action-key"] == f"{PROVIDER}5", "the one stale trigger was not repaired"
    assert "Project triggers repaired: 1; could not repair: 0." in run.output
    assert "WARNING: project P03499 " in run.output, "the missing-trigger report did not run"


def test_a_repair_xnat_refuses_is_a_warning_and_the_rest_go_on(tmp_path: Path) -> None:
    """One subscription XNAT will not update (its owner cannot be loaded) must not fail every deploy."""
    state = xnat_state(
        [command(5, 5, image=pinned_image())],
        subscriptions=[subscription(1, 4, ["P"]), subscription(3, 4, ["Q"])],
        project_enabled={"P": [4], "Q": [4]},
        faults={"reject_subscription_update": [1]},
    )
    run = configure(tmp_path, state)

    assert run.code == 0, run.output
    assert "WARNING: could not repair the DICOM->NIfTI trigger of project(s) P (subscription 1" in run.output
    assert run.subscription(1)["action-key"] == f"{PROVIDER}4"
    assert run.subscription(3)["action-key"] == f"{PROVIDER}5"
    assert "Project triggers repaired: 1; could not repair: 1." in run.output
    assert "WARNING: invalid DICOM->NIfTI trigger: subscription 1" in run.output, "named again by the report"


# --------------------------------------------------------------------------------------------------
# Names imaging-api and this script must agree on
# --------------------------------------------------------------------------------------------------


def test_the_trigger_name_is_the_one_imaging_api_creates() -> None:
    """The repair finds FLIP's subscriptions by name."""
    script = re.search(r'^DCM2NIIX_SUBSCRIPTION_NAME="([^"]+)"', SCRIPT.read_text(), re.M)
    imaging = re.search(r'^DCM2NIIX_SUBSCRIPTION_NAME = "([^"]+)"', IMAGING_API_PROJECTS.read_text(), re.M)
    assert script, "configure-dcm2niix.sh no longer sets DCM2NIIX_SUBSCRIPTION_NAME"
    assert imaging, "imaging-api no longer defines DCM2NIIX_SUBSCRIPTION_NAME"
    assert script.group(1) == imaging.group(1) == TRIGGER


def test_imaging_api_looks_up_the_wrapper_this_command_registers() -> None:
    """imaging-api finds the trigger's wrapper by name and data type in XNAT's site listing."""
    text = IMAGING_API_PROJECTS.read_text()
    name = re.search(r'^DCM2NIIX_WRAPPER_NAME = "([^"]+)"', text, re.M)
    context = re.search(r'^DCM2NIIX_CONTEXT = "([^"]+)"', text, re.M)
    assert name, "imaging-api no longer defines DCM2NIIX_WRAPPER_NAME"
    assert context, "imaging-api no longer defines DCM2NIIX_CONTEXT"
    (wrapper,) = command_file()["xnat"]
    assert name.group(1) == wrapper["name"]
    assert context.group(1) in wrapper["contexts"]


HUB_ID_PATTERN = "^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"


def test_flip_projects_are_recognised_the_same_way_everywhere() -> None:
    """Which XNAT projects are FLIP's (a hub-id secondary_ID) decides who gets reported and restored."""
    for name, text in (
        ("configure-dcm2niix.sh", SCRIPT.read_text()),
        ("restore_nifti_triggers.py", RESTORE_CLI.read_text()),
    ):
        assert HUB_ID_PATTERN in text, f"{name} no longer recognises FLIP projects by {HUB_ID_PATTERN}"
