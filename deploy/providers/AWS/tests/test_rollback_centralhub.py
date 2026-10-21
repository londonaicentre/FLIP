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
"""``rollback-centralhub`` resets the FLARE fl-server's ``local/`` before it moves a service (FLIP#1390).

Since FLIP#1390 the fl-server writes ``local/flip__p_resources.json`` onto EFS at every start, and an older
fl-server image fails to load its configuration while that file is there. An operator reaches for the
rollback mid-incident, so the target carries the remedy itself: it runs ``scripts/reset-fl-server-local.sh``
first and stops, having moved nothing, if that fails.

These tests run the real target with ``aws`` stubbed on PATH (no credentials, no network) and read back the
ECS calls it made. The script's own behaviour is covered by ``scripts/tests/test_reset_fl_server_local.sh``.
"""

import os
import subprocess
from pathlib import Path

AWS_DIR = Path(__file__).resolve().parents[1]

# The smallest env file the Makefile's parse-time guards accept, as in test_iam_permissions_boundary.py.
STUB_ENV = {
    "AWS_PROFILE": "probe",
    "PROD_AWS_PROFILE": "probe",
    "STAG_AWS_PROFILE": "probe",
    "LZA_AWS_PROFILE": "probe",
    "LZA_STAG_AWS_PROFILE": "probe",
    "LZA_VPC_NAME": "probe-vpc",
    "FLARE_KIT_DATE": "00000000",
    "FLOWER_KIT_DATE": "00000000",
    "FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME": "probe-uploads",
    "FLIP_FL_RESULTS_BUCKET_NAME": "probe-results",
    "FLIP_APP_BUNDLES_BUCKET_NAME": "probe-bundles",
    "AICENTRE_BUCKET_NAME": "probe-aicentre",
    "INTERNAL_SERVICE_KEY": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HASH": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HEADER": "X-Probe",  # pragma: allowlist secret
}

# Every service sits on revision 7 with revision 6 below it, so each rolls back 7 -> 6. The provisioning
# task exits with $RESET_EXIT_CODE.
STUB_AWS = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$CALLS"
service="" family=""
prev=""
for arg in "$@"; do
    case "$prev" in
        --services) service="$arg" ;;
        --family-prefix) family="$arg" ;;
    esac
    prev="$arg"
done
arn="arn:aws:ecs:eu-west-2:000000000000:task-definition"
case "$2" in
    list-task-definition-families) echo "efs-provision-certs" ;;
    describe-services)
        case "$*" in
            *networkConfiguration*) echo '{"awsvpcConfiguration":{"subnets":["subnet-a"]}}' ;;
            *) echo "$arn/$service:7" ;;
        esac ;;
    run-task) echo "arn:aws:ecs:eu-west-2:000000000000:task/flip-cluster/reset" ;;
    wait) ;;
    describe-tasks) echo "$RESET_EXIT_CODE" ;;
    list-task-definitions) printf '["%s/%s:7","%s/%s:6"]\\n' "$arn" "$family" "$arn" "$family" ;;
    update-service) echo "$service" ;;
    deregister-task-definition) echo "INACTIVE" ;;
    *) echo "unexpected aws call: $*" >&2; exit 1 ;;
esac
"""


def _rollback(tmp_path: Path, *, reset_exit_code: str = "0", **variables: str) -> tuple[int, list[str], str]:
    """Run ``make rollback-centralhub`` against the stub; returns its exit code, the aws calls and its output."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(STUB_AWS)
    aws.chmod(0o755)
    calls = tmp_path / "calls"
    calls.touch()
    env_file = tmp_path / ".env.probe-stag"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in STUB_ENV.items()))

    env = {k: v for k, v in os.environ.items() if not k.startswith("TF_VAR_")}
    env.update(PATH=f"{bin_dir}{os.pathsep}{env['PATH']}", CALLS=str(calls), RESET_EXIT_CODE=reset_exit_code)
    args = {"PROD": "stag", "MAIN_ENV_FILE": str(env_file), "FL_BACKEND": "nvflare", **variables}
    result = subprocess.run(
        ["make", "-s", "rollback-centralhub", *(f"{k}={v}" for k, v in args.items())],
        cwd=AWS_DIR,
        env=env,
        text=True,
        capture_output=True,
    )
    return result.returncode, calls.read_text().splitlines(), result.stdout + result.stderr


def _index(calls: list[str], fragment: str) -> int:
    return next(i for i, call in enumerate(calls) if fragment in call)


def test_the_fl_servers_local_is_reset_before_any_service_moves(tmp_path: Path) -> None:
    rc, calls, output = _rollback(tmp_path)

    assert rc == 0, output
    assert _index(calls, "ecs run-task") < _index(calls, "ecs update-service"), calls
    run_task = calls[_index(calls, "ecs run-task")]
    assert "--task-definition efs-provision-certs" in run_task
    assert "--cluster flip-cluster" in run_task
    assert sum("ecs update-service" in call for call in calls) == 3, "every hub service still rolls back"


def test_a_failed_reset_rolls_nothing_back_and_says_how_to_proceed(tmp_path: Path) -> None:
    rc, calls, output = _rollback(tmp_path, reset_exit_code="1")

    assert rc != 0
    assert not any("ecs update-service" in call for call in calls), calls
    assert not any("deregister-task-definition" in call for call in calls), calls
    assert "Nothing has been rolled back" in output
    assert "SKIP_FL_LOCAL_RESET=1" in output


def test_the_reset_can_be_skipped(tmp_path: Path) -> None:
    rc, calls, output = _rollback(tmp_path, SKIP_FL_LOCAL_RESET="1")

    assert rc == 0, output
    assert not any("ecs run-task" in call for call in calls), calls
    assert sum("ecs update-service" in call for call in calls) == 3


def test_a_flower_hub_has_no_nvflare_local_to_reset(tmp_path: Path) -> None:
    rc, calls, output = _rollback(tmp_path, FL_BACKEND="flower")

    assert rc == 0, output
    assert not any("ecs run-task" in call for call in calls), calls
