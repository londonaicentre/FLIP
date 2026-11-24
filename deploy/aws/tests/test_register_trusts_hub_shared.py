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
"""``register-trusts.sh`` fills the whole hub-shared block, or fails naming what it could not (FLIP#1476).

The flip-api task's env carries only some hub-shared keys, so the script refreshes the block from the
operator env file the Makefile exports. These tests run the real script, kit writer and sync with ``aws``
stubbed on PATH (``uv run --no-config`` runs the script with this interpreter).
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "register-trusts.sh"
HUB_SHARED = (
    "AES_KEY_BASE64",
    "CENTRAL_HUB_API_URL",
    "TRUST_API_KEY_HEADER",
    "FL_BACKEND",
    "FLOWER_KIT_DATE",
    "FLARE_KIT_DATE",
    "DOCKER_TAG",
    "DOCKER_FL_TAG",
    "NLB_SUBDOMAIN",
    "FL_SERVER_PORT",
    "FL_EDGE_PORT",
)
# The operator env file (.env.prod) as the Makefile exports it: an NVFLARE hub, so no FLOWER_KIT_DATE.
HUB_ENV = {
    "AES_KEY_BASE64": "a2V5LWZvci10ZXN0cw==",  # pragma: allowlist secret
    "CENTRAL_HUB_API_URL": "https://hub.example.org/api",
    "TRUST_API_KEY_HEADER": "X-Trust-Api-Key",  # pragma: allowlist secret
    "FL_BACKEND": "nvflare",
    "FLARE_KIT_DATE": "20260101",
    "DOCKER_TAG": "v1.2.3",
    "DOCKER_FL_TAG": "v1.2.3",
    "NLB_SUBDOMAIN": "fl.example.org",
    "FL_SERVER_PORT": "8002",
}
# What the task's env yields: only these two hub-shared keys.
TASK_HUB_SHARED = {"TRUST_API_KEY_HEADER": "X-Trust-Api-Key", "FL_BACKEND": "nvflare"}  # pragma: allowlist secret
METADATA = {"trust_id": "id-1", "trust_name": "Scratch Trust", "fl_kit_slot": "site-1", "fl_kit_slot_number": 1}
CREDENTIALS = {"trust_api_key": "api-key-k1", "trust_internal_service_key": "int-key-k2"}  # pragma: allowlist secret

STUB_AWS = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$CALLS"
case "$1 $2" in
    "ecs describe-services")
        echo '{"networkConfiguration":{"awsvpcConfiguration":
              {"subnets":["s"],"securityGroups":["g"],"assignPublicIp":"DISABLED"}}}' ;;
    "ecs run-task") echo "arn:aws:ecs:eu-west-2:000000000000:task/flip-cluster/abc" ;;
    "ssm get-parameter") printf '%s\\n' "$KIT_JSON" ;;
esac
"""
STUB_UV = f"""#!/usr/bin/env bash
shift 2  # run --no-config
exec {sys.executable} "$@"
"""

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


def _register(tmp_path: Path, kit: dict, **env: str) -> tuple[int, str, str, str]:
    """Run the script as ``make register-trusts KIT=T0 HUB=prod`` does; returns code, output, kit file, aws calls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("aws", STUB_AWS), ("uv", STUB_UV)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    calls = tmp_path / "calls"
    calls.touch()
    kit_file = tmp_path / ".env.T0.prod"
    kit_file.write_text("TRUST_NAME=Scratch Trust\n" + "".join(f"{k}=<run-make-register-trust>\n" for k in HUB_SHARED))
    base = {k: v for k, v in os.environ.items() if k not in HUB_SHARED}
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **base,
            **HUB_ENV,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "AWS_PROFILE": "probe",
            "AWS_REGION": "eu-west-2",
            "KIT": "T0",
            "ENV_CLASS": "prod",
            "MAIN_ENV_FILE": "/hub/.env.prod",
            "KIT_FILES": str(kit_file),
            "KIT_JSON": json.dumps([{**kit, "hub_shared": TASK_HUB_SHARED}]),
            **env,
        },
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout + result.stderr, kit_file.read_text(), calls.read_text()


@pytest.mark.parametrize("kit", [METADATA, {**METADATA, **CREDENTIALS}], ids=["already-registered", "first"])
def test_every_hub_shared_key_is_filled_from_the_env_file(tmp_path: Path, kit: dict) -> None:
    code, out, content, _ = _register(tmp_path, kit)
    assert code == 0, out
    assert "<run-make-register-trust>" not in content
    for key, value in HUB_ENV.items():
        assert f"{key}={value}\n" in content
    assert "FL_EDGE_PORT=8002\n" in content
    assert "FLOWER_KIT_DATE=\n" in content


def test_an_unresolved_key_fails_naming_it_and_its_source(tmp_path: Path) -> None:
    code, out, content, _ = _register(tmp_path, METADATA, NLB_SUBDOMAIN="")
    assert code == 1, out
    assert "NLB_SUBDOMAIN" in out
    assert "/hub/.env.prod" in out
    assert "register-trusts complete" not in out
    assert HUB_ENV["AES_KEY_BASE64"] not in out


@pytest.mark.parametrize("kit", [METADATA, {**METADATA, **CREDENTIALS}], ids=["already-registered", "first"])
def test_a_failed_sync_names_the_recovery_without_printing_values(tmp_path: Path, kit: dict) -> None:
    code, out, content, _ = _register(tmp_path, kit, NLB_SUBDOMAIN="", HUB="prod")
    assert code == 1, out
    assert "make sync-trust-kit KIT=T0 HUB=prod" in out
    assert ("The trust IS registered" in out) == ("trust_api_key" in kit), out
    for value in (*CREDENTIALS.values(), HUB_ENV["AES_KEY_BASE64"]):
        assert value not in out
    if "trust_api_key" in kit:
        assert f"TRUST_API_KEY={CREDENTIALS['trust_api_key']}" in content, "the credentials stay in the kit"


def test_the_aes_key_never_travels_in_the_run_task_overrides(tmp_path: Path) -> None:
    code, out, _, calls = _register(tmp_path, METADATA)
    assert code == 0, out
    run_task = calls.split("ecs run-task", 1)[1].split("ecs wait tasks-stopped", 1)[0]
    assert "AES_KEY_BASE64" not in run_task
    assert HUB_ENV["AES_KEY_BASE64"] not in calls
