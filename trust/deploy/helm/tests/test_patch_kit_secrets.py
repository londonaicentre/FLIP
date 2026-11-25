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

The FL client reads `TRUST_INTERNAL_SERVICE_KEY` and its header name from the chart Secret by
`secretKeyRef`, like the three APIs, and its pod template carries no checksum of that Secret. The
target restarted only the APIs, so after a key rotation the client kept the old key in its
environment and every call it made to imaging-api and data-access-api was refused, with the deploy
exiting 0. `deploy-trust-k8s` then discarded the target's exit status altogether.

Both targets are run for real against stubs. The target runs on every `deploy-trust-k8s KIT=`, and
restarting an FL client ends an in-flight FL job, so a client is restarted only when the Secret no
longer holds what it was last restarted with: a fingerprint of the two entries, kept as an
annotation on the client's Deployment. That holds whichever verb patched the Secret (`sync-kit`
patches it and restarts nothing) and across a run that patched and then failed. A restarted
fl-client is waited on only when it was ready before the restart: Pending is its resting state while
its kit is not staged or no node has a free GPU, and a `rollout status` on that client can only
time out.
"""

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")

ANNOTATION = "flip/internal-service-key-sha256"
API_NAMES = [f"trust-release-flip-trust-{svc}" for svc in ("data-access-api", "imaging-api", "trust-api")]
APIS = [f"deployment/{name}" for name in API_NAMES]
API_ROWS = "".join(f"{name.removeprefix('trust-release-flip-trust-')}={name}=1= " for name in API_NAMES)
FL = "rel-fl-client-net-1"
OTHER = "xnat-web=trust-release-flip-trust-xnat-web=1= "


def fingerprint(entries: str) -> str:
    """What the target records: sha256 over the Secret's `<key>:<header>` as kubectl prints them."""
    return hashlib.sha256(entries.encode()).hexdigest()


OLD, NEW = fingerprint("old-key:hdr"), fingerprint("new-key:hdr")

# sync_k8s_kit.py runs through `uv run`; the stub stands in for the Secret patch, leaving ENTRIES_AFTER
# as the Secret's `<key>:<header>` (the Secret untouched with ENTRIES_AFTER empty).
UV_STUB = """#!/bin/sh
printf 'uv %s\\n' "$*" >> "$CALL_LOG"
[ -z "$ENTRIES_AFTER" ] || printf '%s' "$ENTRIES_AFTER" > "$SECRET_FILE"
exit "${UV_RC:-0}"
"""

# kubectl: `get secret` prints the Secret's two entries, nothing when there is no Secret (as with
# --ignore-not-found), and fails on its SECRET_READ_FAILS-th call; the Deployment listing prints
# DEPLOYMENTS (`<component>=<name>=<readyReplicas>=<recorded fingerprint>` each, as the target's
# jsonpath asks for it) or fails with GET_RC; RESTART_FAILS and STATUS_FAILS fail that Deployment's
# `rollout restart` / `rollout status`.
KUBECTL_STUB = """#!/bin/sh
printf 'kubectl %s\\n' "$*" >> "$CALL_LOG"
case "$1 $2" in
  "get secret")
    echo x >> "$SECRET_FILE.reads"
    [ "$(wc -l < "$SECRET_FILE.reads" | tr -d ' ')" != "$SECRET_READ_FAILS" ] || { echo 'error: timeout' >&2; exit 1; }
    cat "$SECRET_FILE" 2>/dev/null ;;
  "get deployment") printf '%s' "$DEPLOYMENTS"; exit "${GET_RC:-0}" ;;
  "rollout restart") [ "$3" != "$RESTART_FAILS" ] || exit 1 ;;
  "rollout status") [ "$3" != "$STATUS_FAILS" ] || exit 1 ;;
esac
exit 0
"""

# make: stands in for $(MAKE) in deploy-trust-k8s, failing the sub-targets named in FAIL_TARGETS.
MAKE_STUB = """#!/bin/sh
printf 'make %s\\n' "$*" >> "$CALL_LOG"
case " $FAIL_TARGETS " in *" $1 "*) exit 2 ;; esac
exit 0
"""


def _stub(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _make(
    tmp_path: Path,
    target: str,
    *args: str,
    entries_before: str | None = "old-key:hdr",
    fl: str = "",
    **env_overrides: str,
) -> tuple[subprocess.CompletedProcess, str]:
    """Run the target. The Secret holds `entries_before` (None: no Secret yet) and, unless
    ENTRIES_AFTER says otherwise, the patch rotates the key. `fl` is the fl-client rows of the
    Deployment listing, added to the three APIs'."""
    log = tmp_path / "calls.log"
    log.write_text("")
    secret = tmp_path / "secret-entries"
    if entries_before is not None:
        secret.write_text(entries_before)
    env_overrides = {
        "SECRET_FILE": str(secret),
        "ENTRIES_AFTER": "new-key:hdr",
        "DEPLOYMENTS": API_ROWS + OTHER + fl,
        **env_overrides,
    }
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


def _recorded(calls: str) -> list[str]:
    """`<deployment>=<value>` for every fingerprint annotation written, in order."""
    return [
        f"{line.split()[2]}={line.rsplit('=', 1)[1]}"
        for line in calls.splitlines()
        if line.startswith("kubectl annotate ") and f" {ANNOTATION}=" in line
    ]


# ---------------------------------------------------------------- when the FL client restarts


def test_a_client_last_restarted_on_another_key_is_restarted_whoever_patched_the_secret(tmp_path):
    """`sync-kit` patches the Secret and restarts nothing, so this run finds the Secret already on
    the new key: comparing the Secret before and after the patch saw no change there."""
    result, calls = _make(tmp_path, "patch-kit-secrets", entries_before="new-key:hdr", fl=f"fl-client={FL}=1={OLD} ")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{FL}: internal service key changed: restarting the FL client" in result.stdout
    assert _restarted(calls) == [*APIS, f"deployment/{FL}"]
    assert _waited(calls) == [*APIS, f"deployment/{FL}"]
    assert _recorded(calls)[-1] == f"deployment/{FL}={NEW}"


def test_a_changed_header_name_restarts_the_client_too(tmp_path):
    """The client reads `trust-internal-service-key-header` by secretKeyRef as well."""
    result, calls = _make(
        tmp_path, "patch-kit-secrets", fl=f"fl-client={FL}=1={OLD} ", ENTRIES_AFTER="old-key:other-hdr"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"deployment/{FL}" in _restarted(calls)
    assert _recorded(calls)[-1] == f"deployment/{FL}={fingerprint('old-key:other-hdr')}"


def test_a_client_already_on_the_secrets_key_is_left_running(tmp_path):
    """Every `deploy-trust-k8s KIT=` runs this target; a restart here ends an in-flight FL job."""
    result, calls = _make(tmp_path, "patch-kit-secrets", fl=f"fl-client={FL}=1={NEW} ")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{FL}: internal service key unchanged: FL client left running" in result.stdout
    assert _restarted(calls) == APIS
    assert _waited(calls) == APIS
    assert _recorded(calls) == []


def test_a_client_with_nothing_recorded_yet_is_recorded_and_left_running(tmp_path):
    """The first run on a trust deployed before the annotation existed must not end its FL job."""
    result, calls = _make(tmp_path, "patch-kit-secrets", fl=f"fl-client={FL}=1= ", ENTRIES_AFTER="")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{FL}: internal service key unchanged: FL client left running" in result.stdout
    assert _restarted(calls) == APIS
    assert _recorded(calls) == [f"deployment/{FL}={OLD}"]


@pytest.mark.parametrize("entries_before", ["old-key:hdr", None], ids=["rotated-in-this-run", "no-secret-before"])
def test_a_client_with_nothing_recorded_is_restarted_when_this_run_changed_the_secret(tmp_path, entries_before):
    result, calls = _make(tmp_path, "patch-kit-secrets", entries_before=entries_before, fl=f"fl-client={FL}=1= ")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"deployment/{FL}" in _restarted(calls)
    assert _recorded(calls)[-1] == f"deployment/{FL}={NEW}"


def test_a_restart_that_failed_is_repaired_by_running_the_target_again(tmp_path):
    """The first run patched the Secret, so the re-run its message prints sees no change in it. The
    client is marked before its restart, and that mark is what the re-run acts on."""
    first, calls = _make(tmp_path, "patch-kit-secrets", fl=f"fl-client={FL}=1= ", RESTART_FAILS=f"deployment/{FL}")

    assert first.returncode != 0
    assert _recorded(calls) == [f"deployment/{FL}=pending"]

    again, calls = _make(tmp_path, "patch-kit-secrets", entries_before=None, fl=f"fl-client={FL}=1=pending ")

    assert again.returncode == 0, again.stdout + again.stderr
    assert f"deployment/{FL}" in _restarted(calls)
    assert _recorded(calls)[-1] == f"deployment/{FL}={NEW}"


@pytest.mark.parametrize("ready", ["", "0"], ids=["no-ready-replicas-field", "zero-ready"])
def test_an_fl_client_that_was_not_ready_is_restarted_but_not_waited_on(tmp_path, ready):
    """Awaiting its kit or a free GPU: the restart only replaces a Pending pod, and waiting for it
    would fail every deploy of that trust after 120 s."""
    result, calls = _make(tmp_path, "patch-kit-secrets", fl=f"fl-client={FL}={ready}={OLD} ")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"deployment/{FL}" in _restarted(calls)
    assert _waited(calls) == APIS


def test_the_entries_are_compared_without_being_printed_or_passed_as_an_argument(tmp_path):
    result, calls = _make(tmp_path, "patch-kit-secrets", fl=f"fl-client={FL}=1={OLD} ")

    for value in ("old-key", "new-key"):
        assert value not in result.stdout + result.stderr + calls
    reads = [line for line in calls.splitlines() if line.startswith("kubectl get secret")]
    assert len(reads) == 2
    for read in reads:
        assert "trust-release-flip-trust-secrets" in read
        assert "--ignore-not-found" in read
        assert "{.data.trust-internal-service-key}:{.data.trust-internal-service-key-header}" in read


# ---------------------------------------------------------------- what else is restarted


def test_the_deployments_are_found_by_the_releases_label(tmp_path):
    _, calls = _make(tmp_path, "patch-kit-secrets", "RELEASE_NAME=rel", "NAMESPACE=ns")

    listing = next(line for line in calls.splitlines() if line.startswith("kubectl get deployment"))
    assert "-n ns" in listing
    assert "-l app.kubernetes.io/instance=rel " in listing


def test_only_the_three_apis_restart_on_every_run(tmp_path):
    """Nothing else in the release (xnat-web here) is touched, and no FL client means none restarted."""
    result, calls = _make(tmp_path, "patch-kit-secrets")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _restarted(calls) == APIS
    assert _waited(calls) == APIS


def test_an_api_the_release_does_not_render_is_not_an_error(tmp_path):
    """trustApi.enabled=false or dataAccessApi.enabled=false: restart what exists."""
    result, calls = _make(
        tmp_path, "patch-kit-secrets", DEPLOYMENTS="imaging-api=trust-release-flip-trust-imaging-api=1= "
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _restarted(calls) == ["deployment/trust-release-flip-trust-imaging-api"]


# ---------------------------------------------------------------- what fails it


def test_a_failed_secret_patch_restarts_nothing(tmp_path):
    result, calls = _make(tmp_path, "patch-kit-secrets", UV_RC="1")

    assert result.returncode != 0
    assert _restarted(calls) == []


@pytest.mark.parametrize(("read", "patched"), [("1", False), ("2", True)], ids=["before-the-patch", "after-the-patch"])
def test_a_secret_that_cannot_be_read_stops_the_target_and_restarts_nothing(tmp_path, read, patched):
    """A failed read is not "no Secret": taken as a change, a kubectl timeout restarted a running client."""
    result, calls = _make(tmp_path, "patch-kit-secrets", fl=f"fl-client={FL}=1={OLD} ", SECRET_READ_FAILS=read)

    assert result.returncode != 0
    assert "could not read trust-release-flip-trust-secrets" in result.stdout
    assert ("uv run" in calls) is patched
    assert _restarted(calls) == []
    assert _recorded(calls) == []


def test_no_secret_after_the_patch_stops_the_target(tmp_path):
    result, calls = _make(tmp_path, "patch-kit-secrets", entries_before=None, ENTRIES_AFTER="")

    assert result.returncode != 0
    assert _restarted(calls) == []


def test_a_release_with_no_trust_api_fails(tmp_path):
    """A mistyped RELEASE_NAME: the patch creates a stray Secret and the listing finds nothing."""
    result, calls = _make(tmp_path, "patch-kit-secrets", "RELEASE_NAME=typo", entries_before=None, DEPLOYMENTS="")

    assert result.returncode != 0
    assert "no trust API Deployment for release typo" in result.stdout
    assert _restarted(calls) == []


def test_an_unreadable_cluster_fails_before_any_restart(tmp_path):
    result, calls = _make(tmp_path, "patch-kit-secrets", GET_RC="1")

    assert result.returncode != 0
    assert _restarted(calls) == []


@pytest.mark.parametrize("name", [APIS[0], f"deployment/{FL}"], ids=["api", "fl-client"])
def test_a_deployment_that_does_not_come_back_fails_the_target(tmp_path, name):
    """The loop's status used to be its last iteration's, so only imaging-api could fail it."""
    result, _ = _make(tmp_path, "patch-kit-secrets", fl=f"fl-client={FL}=1={OLD} ", STATUS_FAILS=name)

    assert result.returncode != 0, result.stdout + result.stderr


# ---------------------------------------------------------------- deploy-trust-k8s reports it


def _deploy_trust_k8s(tmp_path: Path, fail_targets: str = "") -> tuple[subprocess.CompletedProcess, list[str]]:
    make = _stub(tmp_path / "make-stub", MAKE_STUB)
    result, calls = _make(tmp_path, "deploy-trust-k8s", f"MAKE={make}", FAIL_TARGETS=fail_targets)
    return result, [line.split()[1] for line in calls.splitlines() if line.startswith("make ")]


def test_deploy_trust_k8s_succeeds_when_every_step_does(tmp_path):
    result, targets = _deploy_trust_k8s(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert targets == ["sync-kit-override", "deploy-release", "xnat-init", "patch-kit-secrets"]


@pytest.mark.parametrize(
    "failed", ["patch-kit-secrets", "xnat-init patch-kit-secrets"], ids=["alone", "after-xnat-init"]
)
def test_deploy_trust_k8s_fails_and_says_so_when_the_key_patch_failed_on_a_live_release(tmp_path, failed):
    result, _ = _deploy_trust_k8s(tmp_path, failed)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "patch-kit-secrets failed" in result.stdout
    assert "make -C trust/deploy/helm patch-kit-secrets KIT=ABC HUB=stag" in result.stdout


def test_a_release_that_did_not_land_is_the_only_failure_reported(tmp_path):
    """With no release there is nothing to patch, and a "Re-run patch-kit-secrets" line sends the
    operator after the wrong step."""
    result, _ = _deploy_trust_k8s(tmp_path, "deploy-release patch-kit-secrets")

    assert result.returncode != 0
    assert "the release did not land" in result.stdout
    assert "patch-kit-secrets failed" not in result.stdout


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
