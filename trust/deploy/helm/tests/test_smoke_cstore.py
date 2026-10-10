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

"""smoke-cstore.sh run end to end against a fake kubectl (FLIP#1411).

XNAT 1.10 writes dicom.log only when something goes wrong, so a working store leaves it empty;
its SCP records each object it accepts in received.log. The smoke used to take dicom.log growth
as proof of receipt and so failed every healthy trust. Receipt now comes from received.log, and
the new dicom.log lines are still scanned for the importer failures of FLIP#1228.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
SMOKE = CHART_DIR / "scripts" / "smoke-cstore.sh"

# Stands in for kubectl: answers the pod/Secret lookups, keeps received.log's line count in a
# file that grows by one when Orthanc's store is called (unless RECEIVED_GROWS=0), and returns
# NEW_DICOM_LINES as whatever the transfer appended to dicom.log.
FAKE_KUBECTL = textwrap.dedent(
    r"""
    #!/usr/bin/env bash
    state="$FAKE_STATE"
    args="$*"
    case "$args" in
      *"get pods"*orthanc*) printf 'orthanc-0' ;;
      *"get pods"*xnat-web*) printf 'xnat-web-0' ;;
      *"get deploy"*) printf 'trust-secrets' ;;
      *"get secret"*) printf '%s' '{"flip":"s3cret"}' | base64 ;;
      *exec*received.log*) cat "$state/received" ;;
      *exec*"wc -l"*dicom.log*) printf '0' ;;
      *exec*"tail -n"*dicom.log*) printf '%s' "${NEW_DICOM_LINES:-}" ;;
      *exec*/instances*) cat >/dev/null; printf '["0a1b2c3d-0a1b2c3d-0a1b2c3d-0a1b2c3d-0a1b2c3d"]' ;;
      *exec*/store*)
        cat >/dev/null
        if [ "${RECEIVED_GROWS:-1}" = 1 ]; then echo $(( $(cat "$state/received") + 1 )) > "$state/received"; fi
        printf '{"FailedInstancesCount" : 0, "InstancesCount" : 1}' ;;
      *) echo "fake kubectl: unexpected call: $args" >&2; exit 1 ;;
    esac
    """
).lstrip()


def run_smoke(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(FAKE_KUBECTL)
    kubectl.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    (state / "received").write_text("3\n")
    full_env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_STATE": str(state),
        "SETTLE_SECONDS": "0",
        "RECEIPT_TIMEOUT_SECONDS": "2",
        **env,
    }
    return subprocess.run(["bash", str(SMOKE)], env=full_env, capture_output=True, text=True, timeout=60)


def test_a_store_xnat_received_passes_although_dicom_log_stayed_empty(tmp_path: Path) -> None:
    result = run_smoke(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "received.log" in result.stdout


def test_a_store_xnat_never_recorded_fails(tmp_path: Path) -> None:
    result = run_smoke(tmp_path, RECEIVED_GROWS="0")

    assert result.returncode != 0
    assert "received.log" in result.stdout + result.stderr


@pytest.mark.parametrize("signature", ["java.lang.AbstractMethodError: x", "unable to read DICOM object null"])
def test_an_importer_failure_fails_even_when_the_object_was_received(tmp_path: Path, signature: str) -> None:
    result = run_smoke(tmp_path, NEW_DICOM_LINES=f"ERROR {signature}\n")

    assert result.returncode != 0
    assert "importer failure" in result.stdout + result.stderr
