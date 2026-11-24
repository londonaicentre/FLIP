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

"""`patch-kit-secrets` restarts everything that holds the trust's keys, and its failure is reported (FLIP#2047).

The FL client reads `TRUST_INTERNAL_SERVICE_KEY` from the chart Secret by `secretKeyRef`, like the
three APIs, and its pod template carries no checksum of that Secret. The target restarted only the
APIs, so after a key rotation the client kept the old key in its environment and every call it made
to imaging-api and data-access-api was refused, with the deploy exiting 0. `deploy-trust-k8s` then
discarded the target's exit status altogether.

Both targets are run for real against stubs. A restarted fl-client is waited on only when it was
ready before the restart: Pending is its resting state while its kit is not staged or no node has a
free GPU, and a `rollout status` on that client can only time out.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")

API_DEPLOYMENTS = [f"trust-release-flip-trust-{svc}" for svc in ("trust-api", "data-access-api", "imaging-api")]

# sync_k8s_kit.py runs through `uv run`; the stub stands in for the Secret patch.
UV_STUB = """#!/bin/sh
printf 'uv %s\\n' "$*" >> "$CALL_LOG"
exit "${UV_RC:-0}"
"""

# kubectl: the fl-client listing prints FL_CLIENTS (`<name>=<readyReplicas>` per Deployment, as the
# target's jsonpath asks for it), or fails with GET_RC; `rollout status` of STATUS_FAILS fails.
KUBECTL_STUB = """#!/bin/sh
printf 'kubectl %s\\n' "$*" >> "$CALL_LOG"
case "$1 $2" in
  "get deployment") printf '%s' "$FL_CLIENTS"; exit "${GET_RC:-0}" ;;
  "rollout status") [ "$3" != "deployment/$STATUS_FAILS" ] || exit 1 ;;
esac
exit 0
"""

# make: stands in for $(MAKE) in deploy-trust-k8s, failing the sub-target named by FAIL_TARGET.
MAKE_STUB = """#!/bin/sh
printf 'make %s\\n' "$*" >> "$CALL_LOG"
[ "$1" != "$FAIL_TARGET" ] || exit 2
exit 0
"""


def _stub(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _make(tmp_path: Path, target: str, *args: str, **env_overrides: str) -> tuple[subprocess.CompletedProcess, str]:
    log = tmp_path / "calls.log"
    log.write_text("")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stub(bin_dir / "uv", UV_STUB)
    kubectl = _stub(bin_dir / "kubectl", KUBECTL_STUB)
    env = dict(os.environ, CALL_LOG=str(log), PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", **env_overrides)
    result = subprocess.run(
        ["make", "-C", str(CHART_DIR), target, "KIT=ABC", "HUB=stag", f"KUBECTL={kubectl}", *args],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    return result, log.read_text()


def _restarted(calls: str) -> list[str]:
    return [line.split()[3] for line in calls.splitlines() if line.startswith("kubectl rollout restart ")]


def _waited(calls: str) -> list[str]:
    return [line.split()[3] for line in calls.splitlines() if line.startswith("kubectl rollout status ")]


# ---------------------------------------------------------------- what is restarted


def test_the_fl_clients_are_restarted_with_the_three_apis(tmp_path):
    result, calls = _make(tmp_path, "patch-kit-secrets", FL_CLIENTS="rel-fl-client-net-1=1 rel-fl-client-net-2=1 ")

    assert result.returncode == 0, result.stdout + result.stderr
    expected = [f"deployment/{name}" for name in [*API_DEPLOYMENTS, "rel-fl-client-net-1", "rel-fl-client-net-2"]]
    assert _restarted(calls) == expected
    assert _waited(calls) == expected


def test_the_fl_clients_are_found_by_the_releases_labels(tmp_path):
    _, calls = _make(tmp_path, "patch-kit-secrets", "RELEASE_NAME=rel", "NAMESPACE=ns")

    listing = next(line for line in calls.splitlines() if line.startswith("kubectl get deployment"))
    assert "-n ns" in listing
    assert "app.kubernetes.io/instance=rel,app.kubernetes.io/component=fl-client" in listing


@pytest.mark.parametrize("ready", ["", "0"], ids=["no-ready-replicas-field", "zero-ready"])
def test_an_fl_client_that_was_not_ready_is_restarted_but_not_waited_on(tmp_path, ready):
    """Awaiting its kit or a free GPU: the restart only replaces a Pending pod, and waiting for it
    would fail every deploy of that trust after 120 s."""
    result, calls = _make(tmp_path, "patch-kit-secrets", FL_CLIENTS=f"rel-fl-client-net-1={ready} ")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "deployment/rel-fl-client-net-1" in _restarted(calls)
    assert _waited(calls) == [f"deployment/{name}" for name in API_DEPLOYMENTS]


def test_a_release_with_no_fl_client_restarts_the_three_apis_only(tmp_path):
    """flClient.enabled=false renders no fl-client Deployment."""
    result, calls = _make(tmp_path, "patch-kit-secrets", FL_CLIENTS="")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _restarted(calls) == [f"deployment/{name}" for name in API_DEPLOYMENTS]


# ---------------------------------------------------------------- what fails it


def test_a_failed_secret_patch_restarts_nothing(tmp_path):
    result, calls = _make(tmp_path, "patch-kit-secrets", UV_RC="1")

    assert result.returncode != 0
    assert "kubectl" not in calls


def test_an_unreadable_cluster_fails_before_any_restart(tmp_path):
    result, calls = _make(tmp_path, "patch-kit-secrets", GET_RC="1")

    assert result.returncode != 0
    assert _restarted(calls) == []


@pytest.mark.parametrize("name", [API_DEPLOYMENTS[0], "rel-fl-client-net-1"], ids=["api", "fl-client"])
def test_a_deployment_that_does_not_come_back_fails_the_target(tmp_path, name):
    """The loop's status used to be its last iteration's, so only imaging-api could fail it."""
    result, _ = _make(tmp_path, "patch-kit-secrets", FL_CLIENTS="rel-fl-client-net-1=1 ", STATUS_FAILS=name)

    assert result.returncode != 0, result.stdout + result.stderr


# ---------------------------------------------------------------- deploy-trust-k8s reports it


def _deploy_trust_k8s(tmp_path: Path, fail_target: str = "") -> tuple[subprocess.CompletedProcess, list[str]]:
    make = _stub(tmp_path / "make-stub", MAKE_STUB)
    result, calls = _make(tmp_path, "deploy-trust-k8s", f"MAKE={make}", FAIL_TARGET=fail_target)
    return result, [line.split()[1] for line in calls.splitlines() if line.startswith("make ")]


def test_deploy_trust_k8s_succeeds_when_every_step_does(tmp_path):
    result, targets = _deploy_trust_k8s(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert targets == ["sync-kit-override", "deploy-release", "xnat-init", "patch-kit-secrets"]


def test_deploy_trust_k8s_fails_when_only_the_key_patch_failed(tmp_path):
    result, _ = _deploy_trust_k8s(tmp_path, "patch-kit-secrets")

    assert result.returncode != 0, result.stdout + result.stderr
    assert "patch-kit-secrets failed" in result.stdout
    assert "make -C trust/deploy/helm patch-kit-secrets KIT=ABC HUB=stag" in result.stdout


@pytest.mark.parametrize(
    ("failed", "ran"),
    [
        ("deploy-release", ["sync-kit-override", "deploy-release", "patch-kit-secrets"]),
        ("xnat-init", ["sync-kit-override", "deploy-release", "xnat-init", "patch-kit-secrets"]),
    ],
)
def test_a_failed_deploy_step_still_patches_the_keys_and_still_fails(tmp_path, failed, ran):
    """A patch that then succeeds must not turn the failed deploy into exit 0 (#595 keeps the patch)."""
    result, targets = _deploy_trust_k8s(tmp_path, failed)

    assert result.returncode != 0, result.stdout + result.stderr
    assert targets == ran
