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

"""``make selftest`` finds the NVFLARE kit through the fl-client's own ``startup/`` mount (FLIP#1958).

The self-test used to hard-code ``/app/startup``; with job pods the chart mounts the kit under the parent's
workspace (#1428), so the "fl kit delivered" check failed on every job-pod trust with the client connected. It now
reads the mount from the pod. This pins what it relies on: in every client mode the fl-client container has exactly
one mount of the kit's ``startup`` subPath, and the self-test resolves the kit there. Skipped without helm; the chart
workflow's helm-template job runs it.
"""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("selftest_k8s", CHART_DIR / "scripts" / "selftest_k8s.py")
selftest = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("selftest_k8s", selftest)
_spec.loader.exec_module(selftest)

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _client_container(*extra: str) -> dict:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    result = subprocess.run([*args, *extra], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"helm template failed: {result.stderr}"
    for doc in yaml.safe_load_all(result.stdout):
        if doc and doc["kind"] == "Deployment" and doc["metadata"]["name"].endswith("-fl-client-net-1"):
            return next(c for c in doc["spec"]["template"]["spec"]["containers"] if c["name"] == "fl-client")
    raise AssertionError("no fl-client Deployment in the render")


class _RenderedPod:
    """The rendered fl-client container, answering the one question the kit check asks of a cluster."""

    def __init__(self, container: dict) -> None:
        self.container = container

    def mounts(self, pod: str, container: str) -> list[dict]:
        return self.container["volumeMounts"] if container == "fl-client" else []


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ((), "/app/startup"),
        (("--set", "flClient.nvflare.jobPods.enabled=true"), "/var/tmp/nvflare/workspace/startup"),
        (("--set", "flClient.kit.source=secret"), "/app/startup"),
    ],
    ids=["in-process", "job-pods", "kit-secret"],
)
def test_the_selftest_finds_the_kit_on_the_mount_the_chart_renders(extra, expected):
    container = _client_container(*extra)
    startup = [m["mountPath"] for m in container["volumeMounts"] if m.get("subPath") == "startup"]

    assert startup == [expected]
    assert selftest.nvflare_startup_dir(_RenderedPod(container), "fl-client-0") == expected
