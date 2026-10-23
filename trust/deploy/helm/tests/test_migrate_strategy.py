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

"""A deploy moves a release's Deployments onto Recreate before Helm 4 applies it (FLIP#1524).

Helm 4 applies the release server-side, which keeps the ``rollingUpdate`` defaults the API server
filled in on a Deployment created as RollingUpdate; a chart that now renders it as Recreate is
then refused (``spec.strategy.rollingUpdate: Forbidden``). FLIP#857 did this to data-access-api.
scripts/migrate-strategy.sh switches such Deployments first. These tests run it against a fake
kubectl that prints a canned render and live state and records every patch.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
SCRIPT = CHART_DIR / "scripts" / "migrate-strategy.sh"
RELEASE = "trust-release"
DAA = f"{RELEASE}-flip-trust-data-access-api"
API = f"{RELEASE}-flip-trust-trust-api"
ORTHANC = f"{RELEASE}-flip-trust-orthanc"

FAKE_KUBECTL = """#!/bin/sh
echo "$*" >> "$KUBECTL_LOG"
case "$*" in
  *"create --dry-run=client"*) cat > /dev/null; printf '%s' "$RENDERED" ;;
  *"get deployment"*) printf '%s' "$LIVE" ;;
  *"patch deployment"*) cat > /dev/null ;;
esac
"""


def _run(tmp_path: Path, rendered: str, live: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(FAKE_KUBECTL)
    kubectl.chmod(0o755)
    log = tmp_path / "kubectl.log"
    env = {
        "PATH": os.environ["PATH"],
        "KUBECTL": f"{kubectl} --context ctx",
        "NAMESPACE": "flip-trust",
        "RELEASE_NAME": RELEASE,
        "KUBECTL_LOG": str(log),
        "RENDERED": rendered,
        "LIVE": live,
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)], input="kind: Deployment\n", capture_output=True, text=True, env=env, timeout=30
    )
    return result, log.read_text().splitlines() if log.exists() else []


def _patched(calls: list[str]) -> list[str]:
    return [c.split("patch deployment ", 1)[1].split()[0] for c in calls if "patch deployment " in c]


def test_a_deployment_the_chart_now_recreates_is_switched_before_the_upgrade(tmp_path):
    rendered = f"Deployment/{DAA} Recreate\nDeployment/{API} \nService/{DAA} \n"
    live = f"{DAA} RollingUpdate\n{API} RollingUpdate\n"

    result, calls = _run(tmp_path, rendered, live)

    assert result.returncode == 0, result.stderr
    assert _patched(calls) == [DAA]
    patch = next(c for c in calls if "patch deployment" in c)
    assert patch.startswith("--context ctx -n flip-trust ")
    assert '"op":"remove","path":"/spec/strategy/rollingUpdate"' in patch
    assert '"path":"/spec/strategy/type","value":"Recreate"' in patch


def test_every_deployment_the_chart_now_recreates_is_switched(tmp_path):
    # The fake kubectl drains stdin on patch, so a patch reading the loop's input would skip ORTHANC.
    rendered = f"Deployment/{DAA} Recreate\nDeployment/{ORTHANC} Recreate\nDeployment/{API} \n"
    live = f"{DAA} RollingUpdate\n{API} RollingUpdate\n{ORTHANC} RollingUpdate\n"

    result, calls = _run(tmp_path, rendered, live)

    assert result.returncode == 0, result.stderr
    assert _patched(calls) == [DAA, ORTHANC]


def test_only_the_releases_own_deployments_are_read(tmp_path):
    _, calls = _run(tmp_path, f"Deployment/{DAA} Recreate\n", "")

    get = next(c for c in calls if "get deployment" in c)
    assert f"-l app.kubernetes.io/instance={RELEASE}" in get
    assert "-n flip-trust" in get


@pytest.mark.parametrize(
    ("rendered", "live"),
    [
        pytest.param(f"Deployment/{DAA} Recreate\n", "", id="first install"),
        pytest.param(f"Deployment/{DAA} Recreate\n", f"{DAA} Recreate\n", id="already Recreate"),
        pytest.param(f"Deployment/{DAA} \n", f"{DAA} RollingUpdate\n", id="chart keeps RollingUpdate"),
        pytest.param(f"Service/{DAA} Recreate\n", f"{DAA} RollingUpdate\n", id="not a Deployment"),
    ],
)
def test_nothing_is_patched_when_the_strategy_does_not_change(tmp_path, rendered, live):
    result, calls = _run(tmp_path, rendered, live)

    assert result.returncode == 0, result.stderr
    assert _patched(calls) == []


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_deploy_switches_strategies_before_helm_upgrades():
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "deploy", "KUBE_CONTEXT=ctx"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout

    migrate = out.index("migrate-strategy.sh")
    assert out.index("template trust-release") < migrate < out.index("upgrade --install")
