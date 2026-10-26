# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""``register-trusts.sh`` with ``ROTATE_KEYS=1`` never discards the only copy of rotated keys (FLIP#1569).

The hub commits the new hash before the kit reaches the kit file, so the SSM parameter the task writes
is the only copy until the write succeeds. These tests run the real script with ``aws`` and ``uv``
stubbed on PATH and read back the calls it made.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "register-trusts.sh"
KIT = [{"trust_name": "Scratch Trust", "trust_api_key": "new", "trust_internal_service_key": "new"}]

STUB_AWS = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$CALLS"
case "$1 $2" in
    "ecs describe-services")
        echo '{"networkConfiguration":{"awsvpcConfiguration":
              {"subnets":["s"],"securityGroups":["g"],"assignPublicIp":"DISABLED"}}}' ;;
    "ecs run-task") echo "arn:aws:ecs:eu-west-2:000000000000:task/flip-cluster/abc" ;;
    "ecs wait") [ "$3" = tasks-stopped ] && exit "${WAIT_EXIT:-0}"; exit 0 ;;
    "ssm get-parameter")
        [ -n "$SSM_ERROR" ] && { echo "An error occurred ($SSM_ERROR)" >&2; exit 254; }
        printf '%s\\n' "$KIT_JSON" ;;
esac
"""
# The kit writer: record that it ran, swallow the kit, exit as told.
STUB_UV = """#!/usr/bin/env bash
printf 'uv %s\\n' "$*" >>"$CALLS"; cat >/dev/null; exit "${UV_EXIT:-0}"
"""

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


def _rotate(tmp_path: Path, kits: int = 1, **env: str) -> tuple[int, list[str], str]:
    """Run the script as ``make rotate-trust-keys`` does; returns its exit code, the calls and its output."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("aws", STUB_AWS), ("uv", STUB_UV)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    calls = tmp_path / "calls"
    calls.touch()
    kit_files = []
    for i in range(kits):
        kit_file = tmp_path / f".env.T{i}.stag"
        kit_file.write_text(f"TRUST_NAME=Scratch Trust {i}\n")
        kit_files.append(str(kit_file))
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "AWS_PROFILE": "probe",
            "AWS_REGION": "eu-west-2",
            "KIT": "T0",
            "ROTATE_KEYS": "1",
            "KIT_FILES": " ".join(kit_files),
            "KIT_JSON": json.dumps(KIT),
            **env,
        },
        capture_output=True,
        text=True,
    )
    return result.returncode, calls.read_text().splitlines(), result.stdout + result.stderr


def _ran(calls: list[str], prefix: str) -> bool:
    return any(call.startswith(prefix) for call in calls)


def _failed_after_the_task(code: int, calls: list[str], out: str) -> None:
    """The run failed, and only after the task ran (the window where the hub may have committed)."""
    assert code == 1, out
    assert _ran(calls, "ecs run-task"), out


def test_a_rotation_passes_rotate_writes_the_kit_then_deletes_the_parameter(tmp_path: Path) -> None:
    code, calls, out = _rotate(tmp_path)
    assert code == 0, out
    log = "\n".join(calls)  # the run-task overrides are pretty-printed JSON
    assert '"--rotate"' in log
    assert '"--code"' not in log
    writer = next(i for i, call in enumerate(calls) if call.startswith("uv run"))
    delete = next(i for i, call in enumerate(calls) if call.startswith("ssm delete-parameter"))
    assert writer < delete, "the parameter must outlive the kit write"


def test_a_failed_kit_write_keeps_the_parameter(tmp_path: Path) -> None:
    code, calls, out = _rotate(tmp_path, UV_EXIT="1")
    _failed_after_the_task(code, calls, out)
    assert not _ran(calls, "ssm delete-parameter")
    assert "still in SSM parameter" in out


def test_a_failed_ssm_read_keeps_the_parameter(tmp_path: Path) -> None:
    code, calls, out = _rotate(tmp_path, SSM_ERROR="ThrottlingException")
    _failed_after_the_task(code, calls, out)
    assert not _ran(calls, "ssm delete-parameter")
    assert not _ran(calls, "uv run")


def test_an_absent_parameter_is_a_failed_rotation_with_a_rerun_hint(tmp_path: Path) -> None:
    code, calls, out = _rotate(tmp_path, SSM_ERROR="ParameterNotFound")
    _failed_after_the_task(code, calls, out)
    assert "re-run 'make rotate-trust-keys KIT=T0'" in out
    assert "not registered" not in out


def test_an_expired_waiter_reads_and_deletes_nothing(tmp_path: Path) -> None:
    code, calls, out = _rotate(tmp_path, WAIT_EXIT="255")
    _failed_after_the_task(code, calls, out)
    assert not _ran(calls, "ssm ")
    assert not _ran(calls, "logs delete-log-stream")


def test_a_kit_without_credentials_is_a_failed_rotation(tmp_path: Path) -> None:
    code, calls, out = _rotate(tmp_path, KIT_JSON=json.dumps([{"trust_name": "Scratch Trust"}]))
    _failed_after_the_task(code, calls, out)
    assert not _ran(calls, "uv run")


def test_rotation_refuses_more_than_one_kit(tmp_path: Path) -> None:
    code, calls, out = _rotate(tmp_path, kits=2)
    assert code == 1, out
    assert not _ran(calls, "ecs run-task")
