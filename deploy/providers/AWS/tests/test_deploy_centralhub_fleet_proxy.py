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
"""``deploy-centralhub`` moves Flower's fleet-proxy sidecar to the deployed tag with the SuperLink (FLIP#1517).

The proxy's allowlist must match the SuperLink's flwr, and only Terraform sets the sidecar's image otherwise, so
a ``TAG=`` deploy that swapped the SuperLink alone would leave a proxy 404ing a newer flwr's Fleet routes.

These tests run the real target with ``aws`` and ``curl`` stubbed on PATH (no credentials, no network) and read
back the task definitions it registers. The stub fails ``aws ecs wait`` so the run stops there, before the UI
deploy that follows a successful one.
"""

import json
import os
import subprocess
from pathlib import Path

from test_rollback_centralhub import STUB_ENV

AWS_DIR = Path(__file__).resolve().parents[1]
REGISTRY = "ghcr.io/londonaicentre/"
TAG = "sha-abc1234"
DIGEST = "sha256:" + "a" * 64

STUB_AWS = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$CALLS"
prev="" service="" td="" input=""
for arg in "$@"; do
    case "$prev" in
        --services|--service) service="$arg" ;;
        --task-definition) td="$arg" ;;
        --cli-input-json) input="${arg#file://}" ;;
    esac
    prev="$arg"
done
arn="arn:aws:ecs:eu-west-2:000000000000:task-definition"
case "$2" in
    describe-services) echo "$arn/$service:7" ;;
    describe-task-definition) family="${td##*/}"; cat "$FIXTURES/${family%:*}.json" ;;
    register-task-definition)
        family="$(jq -r .family "$input")"
        cp "$input" "$OUT/$family.json"
        echo "$arn/$family:8" ;;
    update-service) echo "$service" ;;
    wait) exit 1 ;;
    *) echo "unexpected aws call: $*" >&2; exit 1 ;;
esac
"""

# The GHCR token, then the manifest probe's status code: every image is published.
STUB_CURL = """#!/usr/bin/env bash
case "$*" in
    *ghcr.io/token*) echo '{"token":"probe"}' ;;
    *) printf 200 ;;
esac
"""


def _task_definition(family: str, containers: list[tuple[str, str]]) -> dict:
    return {
        "taskDefinition": {
            "taskDefinitionArn": f"arn:aws:ecs:eu-west-2:000000000000:task-definition/{family}:7",
            "revision": 7,
            "status": "ACTIVE",
            "family": family,
            "containerDefinitions": [{"name": name, "image": image, "essential": True} for name, image in containers],
        },
        "tags": [],
    }


def _deploy(tmp_path: Path, fl_server_containers: list[tuple[str, str]], fl_api_image: str) -> dict[str, dict]:
    """Run ``make deploy-centralhub TAG=…`` against the stubs; returns the registered task definitions by family."""
    bin_dir, fixtures, out = tmp_path / "bin", tmp_path / "fixtures", tmp_path / "out"
    for d in (bin_dir, fixtures, out):
        d.mkdir()
    for name, body in (("aws", STUB_AWS), ("curl", STUB_CURL)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    families = {
        "flip-api": [("flip-api", f"{REGISTRY}flip-api:sha-0000000")],
        "fl-api-net-1": [("fl-api-net-1", fl_api_image)],
        "fl-server-net-1": fl_server_containers,
    }
    for family, containers in families.items():
        (fixtures / f"{family}.json").write_text(json.dumps(_task_definition(family, containers)))
    calls = tmp_path / "calls"
    calls.touch()
    env_file = tmp_path / ".env.probe-stag"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in STUB_ENV.items()))

    env = {k: v for k, v in os.environ.items() if not k.startswith("TF_VAR_")}
    env.update(PATH=f"{bin_dir}{os.pathsep}{env['PATH']}", CALLS=str(calls), FIXTURES=str(fixtures), OUT=str(out))
    args = {"PROD": "stag", "MAIN_ENV_FILE": str(env_file), "TAG": TAG}
    result = subprocess.run(
        ["make", "-s", "deploy-centralhub", *(f"{k}={v}" for k, v in args.items())],
        cwd=AWS_DIR,
        env=env,
        text=True,
        capture_output=True,
    )
    # Every service is registered and repointed before the stubbed wait fails the run.
    assert result.returncode != 0, result.stdout + result.stderr
    assert "did not stabilise" in result.stdout + result.stderr, result.stdout + result.stderr
    registered = {path.stem: json.loads(path.read_text()) for path in out.glob("*.json")}
    assert sorted(registered) == sorted(families), result.stdout + result.stderr
    return registered


def _images(task_definition: dict) -> dict[str, str]:
    return {c["name"]: c["image"] for c in task_definition["containerDefinitions"]}


def test_a_flower_deploy_moves_the_fleet_proxy_to_the_deployed_tag(tmp_path: Path) -> None:
    registered = _deploy(
        tmp_path,
        [
            ("fl-server-net-1", f"{REGISTRY}flower-superlink:v1.2.3@{DIGEST}"),
            ("fleet-proxy", f"{REGISTRY}flower-fleet-proxy:v1.2.3"),
        ],
        f"{REGISTRY}flower-fl-api:v1.2.3@{DIGEST}",
    )
    assert _images(registered["fl-server-net-1"]) == {
        "fl-server-net-1": f"{REGISTRY}flower-superlink:{TAG}",
        "fleet-proxy": f"{REGISTRY}flower-fleet-proxy:{TAG}",
    }


def test_the_proxy_keeps_its_own_repository_and_a_registry_port(tmp_path: Path) -> None:
    registered = _deploy(
        tmp_path,
        [
            ("fleet-proxy", f"registry.example:5000/flip/flower-fleet-proxy:sha-0000000@{DIGEST}"),
            ("fl-server-net-1", f"{REGISTRY}flower-superlink:sha-0000000"),
        ],
        f"{REGISTRY}flower-fl-api:sha-0000000",
    )
    proxy_image = _images(registered["fl-server-net-1"])["fleet-proxy"]
    assert proxy_image == f"registry.example:5000/flip/flower-fleet-proxy:{TAG}"


def test_an_nvflare_deploy_swaps_the_server_alone(tmp_path: Path) -> None:
    registered = _deploy(
        tmp_path,
        [("fl-server-net-1", f"{REGISTRY}flare-fl-server:sha-0000000")],
        f"{REGISTRY}flare-fl-api:sha-0000000",
    )
    assert _images(registered["fl-server-net-1"]) == {"fl-server-net-1": f"{REGISTRY}flare-fl-server:{TAG}"}
    assert _images(registered["fl-api-net-1"]) == {"fl-api-net-1": f"{REGISTRY}flare-fl-api:{TAG}"}
    assert _images(registered["flip-api"]) == {"flip-api": f"{REGISTRY}flip-api:{TAG}"}
