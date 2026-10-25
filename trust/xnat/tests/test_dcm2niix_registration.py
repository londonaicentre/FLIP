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

"""dcm2niix registration, on both Container Service backends (FLIP#1451).

Configuring an XNAT used to delete the dcm2niix command and register it again, which gave its wrapper
a new id and so invalidated every project's DICOM->NIfTI trigger. Each test runs
``configure-dcm2niix.sh`` for real against :mod:`fake_xnat`, a miniature XNAT behind a ``curl`` on
``PATH``, and asserts what XNAT ends up holding.
"""

from __future__ import annotations

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


def subscription(sub_id: int, wrapper_id: int, projects: list[str]) -> dict[str, Any]:
    """A subscription shaped as GET /xapi/events/subscriptions returns one: projects only in the filter."""
    return {
        "id": sub_id,
        "name": TRIGGER,
        "active": True,
        "action-key": f"{PROVIDER}{wrapper_id}",
        "attributes": {},
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
def test_an_unchanged_backend_is_not_registered_again(tmp_path: Path, backend: str) -> None:
    """Each POST stores the server under a new id, so a re-run that changed nothing must not POST."""
    first = configure(tmp_path / "one", xnat_state([command(4, 4)]), backend)
    assert first.code == 0, first.output
    second = configure(tmp_path / "two", first.state, backend)

    assert second.code == 0, second.output
    assert not second.made("POST", r"/xapi/docker/server")
    assert second.state["backend"]["id"] == first.state["backend"]["id"]
    assert "already matches the configuration" in second.output
    assert second.made("GET", r"/xapi/docker/server/ping"), "an unchanged backend is still pinged"


def test_a_drifted_backend_is_registered_again(tmp_path: Path) -> None:
    first = configure(tmp_path / "one", xnat_state([command(4, 4)]))
    assert first.code == 0, first.output
    drifted = first.state
    drifted["backend"]["host"] = "unix:///var/run/docker.sock"
    second = configure(tmp_path / "two", drifted)

    assert second.code == 0, second.output
    assert second.made("POST", r"/xapi/docker/server")
    assert "(drifted on host)" in second.output
    assert second.state["backend"]["host"] == "tcp://xnat-socket-proxy:2375"


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
# Names imaging-api and this script must agree on
# --------------------------------------------------------------------------------------------------


def test_the_trigger_name_is_the_one_imaging_api_creates() -> None:
    """The validation finds FLIP's triggers by name."""
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


def test_imaging_api_reconverts_what_the_wrapper_takes_and_writes() -> None:
    """reconvert_nifti launches on scans the wrapper's matcher accepts and waits for the resource it writes."""
    text = IMAGING_API_PROJECTS.read_text()
    held: dict[str, str] = {}
    for key in ("ROOT_ELEMENT", "INPUT_RESOURCE", "OUTPUT_RESOURCE"):
        found = re.search(rf'^DCM2NIIX_{key} = "([^"]+)"', text, re.M)
        assert found, f"imaging-api no longer defines DCM2NIIX_{key}"
        held[key] = found.group(1)
    (wrapper,) = command_file()["xnat"]
    (scan_input,) = wrapper["external-inputs"]
    (derived,) = wrapper["derived-inputs"]
    (handler,) = wrapper["output-handlers"]
    assert held["ROOT_ELEMENT"] == scan_input["name"]
    assert f"'{held['INPUT_RESOURCE']}' in @.resources[*].label" == scan_input["matcher"]
    assert f"@.label == '{held['INPUT_RESOURCE']}'" == derived["matcher"]
    assert held["OUTPUT_RESOURCE"] == handler["label"]
    assert handler["as-a-child-of"] == scan_input["name"]
