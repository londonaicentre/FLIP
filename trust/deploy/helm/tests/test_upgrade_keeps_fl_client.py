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

"""An upgrade never moves the FL kit by accident, and fails when the FL client does not come back.

Seen on a k3s trust (FLIP#1984): a kit staged with KIT_DEST=<path>, then an upgrade without it. The
upgrade set flClient.kitHostPath back to the default, the new fl-client pod could not mount its
kit, the old one was already gone (Recreate), and the upgrade exited 0 because the rollout wait
skips the fl-client. The path now has one home, the kit file's FL_KIT_DIR (FLIP#2042), and a
release that mounts another is refused before helm runs. These run `deploy-release` for real
against stub helm and kubectl binaries.
"""

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("check_kit_path", CHART_DIR / "scripts" / "check_kit_path.py")
assert _spec is not None
assert _spec.loader is not None
check_kit_path = importlib.util.module_from_spec(_spec)
sys.modules["check_kit_path"] = check_kit_path
_spec.loader.exec_module(check_kit_path)

needs_make = pytest.mark.skipif(
    shutil.which("make") is None or shutil.which("python3") is None, reason="make and python3 are required"
)

# helm: `get values` prints $HELM_VALUES (empty: no release yet), `upgrade` succeeds.
HELM_STUB = """#!/bin/sh
printf 'helm %s\\n' "$*" >> "$CALL_LOG"
case "$1" in
  template) echo '' ;;
  get) printf '%s' "$HELM_VALUES" ;;
  status) echo '{}' ;;
esac
exit 0
"""

# kubectl: the release has trust-api and one fl-client. The fl-client reads as ready while
# $FL_READY_FILE exists, which the `helm upgrade` stub cannot touch, so the test removes it to
# play "the upgrade replaced the pod"; its rollout status exits $FL_ROLLOUT_RC.
KUBECTL_STUB = """#!/bin/sh
printf 'kubectl %s\\n' "$*" >> "$CALL_LOG"
case " $* " in *" -f - "*) cat > /dev/null ;; esac
case "$1" in
  get)
    if [ "$2" = deployment ] && [ "$3" = t-fl-client ]; then
      if [ -e "$FL_READY_FILE" ]; then echo '{"spec":{"replicas":1},"status":{"readyReplicas":1}}';
      else echo '{"spec":{"replicas":1},"status":{}}'; fi
    elif [ "$2" = deployment ]; then
      echo '{"items":[
        {"metadata":{"name":"t-trust-api","labels":{"app.kubernetes.io/component":"trust-api"}}},
        {"metadata":{"name":"t-fl-client","labels":{"app.kubernetes.io/component":"fl-client"}}}]}'
    else
      echo '{"items":[]}'
    fi ;;
  rollout)
    case "$*" in *t-fl-client*) exit "${FL_ROLLOUT_RC:-0}" ;; esac ;;
esac
exit 0
"""


def _stub(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _deploy(tmp_path: Path, *make_args: str, fl_ready_before: bool, **env: str):
    log = tmp_path / "calls.log"
    log.write_text("")
    ready = tmp_path / "fl-ready"
    if fl_ready_before:
        ready.write_text("")
    helm = _stub(tmp_path / "helm", HELM_STUB)
    # The fl-client's old pod goes as soon as helm applies the release (Recreate).
    helm.write_text(HELM_STUB.replace("exit 0\n", f'[ "$1" = upgrade ] && rm -f "{ready}"\nexit 0\n', 1))
    kubectl = _stub(tmp_path / "kubectl", KUBECTL_STUB)
    result = subprocess.run(
        [
            "make",
            "-o",
            "preflight",
            "-C",
            str(CHART_DIR),
            "deploy-release",
            "RELEASE_NAME=t",
            f"HELM_BIN={helm}",
            f"KUBECTL={kubectl}",
            *make_args,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "CALL_LOG": str(log), "FL_READY_FILE": str(ready), "HELM_VALUES": "", **env},
    )
    upgrade = next((line for line in log.read_text().splitlines() if line.startswith("helm upgrade")), "")
    return result, upgrade


MOUNTS_DATA = '{"flClient": {"kitHostPath": "/data/flip/fl-kit"}}'


def _kit(tmp_path: Path, fl_kit_dir: str) -> tuple[str, ...]:
    """The make arguments of a deploy from a kit: its kit file, and the override sync-kit writes from it."""
    kit_file = tmp_path / ".env.SITE.prod"
    kit_file.write_text(f"FL_KIT_DIR={fl_kit_dir}\n")
    override = tmp_path / "k8s-trust-SITE.yaml"
    override.write_text(f"flClient:\n  kitHostPath: {fl_kit_dir}\n")
    return ("KIT=SITE", f"KIT_FILE={kit_file}", f"OVERRIDES_FILE={override}")


# ---------------------------------------------------------------- the kit path


@needs_make
def test_a_deploy_from_a_kit_leaves_the_path_to_the_kit_file(tmp_path):
    """No kit-path --set: on the command line it would beat the override sync-kit rendered from FL_KIT_DIR."""
    result, upgrade = _deploy(tmp_path, *_kit(tmp_path, "/data/flip/fl-kit"), fl_ready_before=False)
    assert result.returncode == 0, result.stderr
    assert "k8s-trust-SITE.yaml" in upgrade
    assert "flClient.kitHostPath" not in upgrade


@needs_make
def test_an_upgrade_that_would_move_the_kit_is_refused_before_helm_runs(tmp_path):
    """The #1984 site: the release mounts the path it was given as KIT_DEST, the kit file still says the default."""
    result, upgrade = _deploy(
        tmp_path, *_kit(tmp_path, "/opt/flip/fl-kit"), fl_ready_before=False, HELM_VALUES=MOUNTS_DATA
    )
    assert result.returncode != 0
    assert upgrade == ""
    assert "set FL_KIT_DIR=/data/flip/fl-kit in" in result.stderr
    assert ".env.SITE.prod" in result.stderr


@needs_make
def test_an_upgrade_whose_kit_file_names_the_mounted_path_goes_ahead(tmp_path):
    result, upgrade = _deploy(
        tmp_path, *_kit(tmp_path, "/data/flip/fl-kit"), fl_ready_before=False, HELM_VALUES=MOUNTS_DATA
    )
    assert result.returncode == 0, result.stderr
    assert upgrade


@needs_make
def test_kit_moved_confirms_a_deliberate_move(tmp_path):
    result, upgrade = _deploy(
        tmp_path, *_kit(tmp_path, "/srv/kit"), "KIT_MOVED=1", fl_ready_before=False, HELM_VALUES=MOUNTS_DATA
    )
    assert result.returncode == 0, result.stderr
    assert upgrade


@needs_make
def test_a_deploy_from_a_kit_file_alone_mounts_the_path_it_checked(tmp_path):
    """KIT= with no override: the path rendered is the one compared with the release, the kit file's."""
    kit_file = tmp_path / ".env.SITE.prod"
    kit_file.write_text("FL_KIT_DIR=/data/flip/fl-kit\n")
    result, upgrade = _deploy(
        tmp_path, "KIT=SITE", f"KIT_FILE={kit_file}", fl_ready_before=False, HELM_VALUES=MOUNTS_DATA
    )
    assert result.returncode == 0, result.stderr
    assert "--set flClient.kitHostPath=/data/flip/fl-kit" in upgrade


@needs_make
def test_a_render_with_no_kit_gets_the_default_path(tmp_path):
    result, upgrade = _deploy(tmp_path, fl_ready_before=False)
    assert result.returncode == 0, result.stderr
    assert upgrade.count("--set flClient.kitHostPath=/opt/flip/fl-kit") == 1


@needs_make
@pytest.mark.parametrize("arg", ["KIT_DEST=/srv/kit", "FL_CLIENT_KIT_HOST_PATH=/srv/kit"])
def test_the_retired_path_arguments_are_refused_naming_the_kit_file(tmp_path, arg):
    result = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "deploy-release", arg], capture_output=True, text=True, timeout=60
    )
    assert result.returncode != 0
    assert "FL_KIT_DIR in the kit file" in result.stderr


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ('{"flClient": {"kitHostPath": "/data/kit"}}', "/data/kit"),
        ('{"flClient": {"kitHostPath": "/data/kit", "kit": {"source": "secret"}}}', ""),
        ('{"flClient": {"kitHostPath": "/data/kit", "enabled": false}}', ""),
        ('{"flClient": {"kitHostPath": ""}}', ""),
        ('{"flClient": {"enabled": true}}', ""),
        ('{"flClient": null}', ""),
        ("null", ""),
        ("", ""),
        ("Error: release: not found", ""),
    ],
)
def test_only_a_host_path_the_release_mounts_is_compared(values, expected):
    """A first install, a kit in a Secret (AKS, EKS) and an FL client that is off have no path to move."""
    assert check_kit_path.mounted_path(values) == expected


# ---------------------------------------------------------------- the FL client's rollout


@needs_make
def test_an_upgrade_fails_when_a_serving_fl_client_does_not_come_back(tmp_path):
    result, _ = _deploy(tmp_path, fl_ready_before=True, FL_ROLLOUT_RC="1")
    assert result.returncode != 0
    assert "it was ready before the upgrade" in result.stdout


@needs_make
def test_an_upgrade_waits_for_a_serving_fl_client_and_passes_when_it_returns(tmp_path):
    result, _ = _deploy(tmp_path, fl_ready_before=True)
    assert result.returncode == 0, result.stderr
    assert "✓ deployment/t-fl-client" in result.stdout


@needs_make
def test_an_fl_client_that_was_not_serving_still_does_not_fail_the_upgrade(tmp_path):
    """Its resting states (no free GPU, a kit not staged yet) stay reported only."""
    result, _ = _deploy(tmp_path, fl_ready_before=False, FL_ROLLOUT_RC="1")
    assert result.returncode == 0, result.stderr
    assert "t-fl-client is not ready, not waited on" in result.stdout
