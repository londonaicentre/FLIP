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

"""An upgrade keeps the FL client where it was, and fails when it does not come back (FLIP#1984).

Seen on a k3s trust: a kit staged with KIT_DEST=<path>, then an upgrade without it. The upgrade set
flClient.kitHostPath back to the default, the new fl-client pod could not mount its kit, the old
one was already gone (Recreate), and the upgrade exited 0 because the rollout wait skips the
fl-client. These run `deploy-release` for real against stub helm and kubectl binaries.
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

_spec = importlib.util.spec_from_file_location("keep_kit_path", CHART_DIR / "scripts" / "keep_kit_path.py")
assert _spec is not None
assert _spec.loader is not None
keep_kit_path = importlib.util.module_from_spec(_spec)
sys.modules["keep_kit_path"] = keep_kit_path
_spec.loader.exec_module(keep_kit_path)

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
    upgrade = next(line for line in log.read_text().splitlines() if line.startswith("helm upgrade"))
    return result, upgrade


def _kit_path(upgrade: str) -> str:
    """The value Helm ends up with: the last --set of flClient.kitHostPath wins."""
    return upgrade.split("--set flClient.kitHostPath=")[-1].split()[0]


# ---------------------------------------------------------------- the kit path


@needs_make
def test_an_upgrade_without_kit_dest_keeps_the_releases_kit_path(tmp_path):
    result, upgrade = _deploy(
        tmp_path, fl_ready_before=False, HELM_VALUES='{"flClient": {"kitHostPath": "/data/flip/fl-kit"}}'
    )
    assert result.returncode == 0, result.stderr
    assert _kit_path(upgrade) == "/data/flip/fl-kit"
    assert "Keeping flClient.kitHostPath=/data/flip/fl-kit" in result.stderr


@needs_make
def test_a_data_root_added_to_a_live_releases_kit_moves_nothing(tmp_path):
    """FLIP#2113: the root's fl-kit/ is only a default, so the release keeps the path it mounts."""
    kit_file = tmp_path / ".env.ABC.prod"
    kit_file.write_text("FLIP_DATA_ROOT=/data/R\n")
    result, upgrade = _deploy(
        tmp_path,
        "KIT=ABC",
        f"KIT_FILE={kit_file}",
        fl_ready_before=False,
        HELM_VALUES='{"flClient": {"kitHostPath": "/opt/flip/fl-kit"}}',
    )
    assert result.returncode == 0, result.stderr
    assert _kit_path(upgrade) == "/opt/flip/fl-kit"


@needs_make
@pytest.mark.parametrize("arg", ["KIT_DEST=/srv/kit", "FL_CLIENT_KIT_HOST_PATH=/srv/kit"])
def test_passing_a_kit_path_moves_it(tmp_path, arg):
    result, upgrade = _deploy(
        tmp_path, arg, fl_ready_before=False, HELM_VALUES='{"flClient": {"kitHostPath": "/data/flip/fl-kit"}}'
    )
    assert result.returncode == 0, result.stderr
    assert _kit_path(upgrade) == "/srv/kit"


@needs_make
def test_a_first_install_takes_the_default_kit_path(tmp_path):
    result, upgrade = _deploy(tmp_path, fl_ready_before=False)
    assert result.returncode == 0, result.stderr
    assert _kit_path(upgrade) == "/opt/flip/fl-kit"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ('{"flClient": {"kitHostPath": "/data/kit"}}', "/data/kit"),
        ('{"flClient": {"kitHostPath": ""}}', ""),
        ('{"flClient": {"enabled": true}}', ""),
        ('{"flClient": null}', ""),
        ("null", ""),
        ("", ""),
        ("Error: release: not found", ""),
    ],
)
def test_only_a_path_the_release_has_is_kept(values, expected):
    assert keep_kit_path.kept_path(values) == expected


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
