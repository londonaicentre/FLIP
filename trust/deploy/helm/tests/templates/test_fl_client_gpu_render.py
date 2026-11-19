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

"""The fl-client's GPU is a Kubernetes allocation, not a pin on the node's GPU 0 (FLIP#1766).

With ``flClient.gpu.enabled`` the chart used to render only an annotation, a toleration and
``NVIDIA_VISIBLE_DEVICES=0``. Kubernetes reserved nothing: on a cluster whose GPUs the NVIDIA device plugin hands
out, the pod could land on a node without a GPU, or take physical GPU 0 from under another workload. By default
(``allocation: devicePlugin``) the container now requests ``nvidia.com/gpu`` and leaves ``NVIDIA_VISIBLE_DEVICES``
to the device plugin, since a value in the pod spec would override the device the plugin allocated.
``allocation: runtime`` keeps the old shape for a node with no device plugin and ``nvidia`` as its default runtime
(single-node k3s). Skipped without helm; the chart workflow's helm-template job runs it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
KIT_HOST_PATH = "/opt/flip/fl-kit"
GPU_ON = ("--set", "flClient.gpu.enabled=true", "--set", "flClient.gpu.count=1")

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _helm(*extra: str) -> subprocess.CompletedProcess:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", f"flClient.kitHostPath={KIT_HOST_PATH}"]
    return subprocess.run([*args, *extra], capture_output=True, text=True, timeout=120)


def _container(*extra: str) -> dict:
    result = _helm(*extra)
    assert result.returncode == 0, f"helm template failed: {result.stderr}"
    for doc in yaml.safe_load_all(result.stdout):
        if (
            doc
            and doc.get("kind") == "Deployment"
            and doc["metadata"]["labels"].get("app.kubernetes.io/component") == "fl-client"
        ):
            return next(c for c in doc["spec"]["template"]["spec"]["containers"] if c["name"] == "fl-client")
    raise AssertionError("no fl-client Deployment in the render")


def _env(container: dict) -> dict:
    return {e["name"]: e.get("value") for e in container.get("env", [])}


def test_an_enabled_gpu_is_requested_from_the_device_plugin_by_default():
    container = _container(*GPU_ON)

    assert container["resources"]["limits"]["nvidia.com/gpu"] == "1"
    # The device plugin names the allocated device; a value in the spec would override it with physical GPU 0.
    assert "NVIDIA_VISIBLE_DEVICES" not in _env(container)
    assert _env(container)["CUDA_VISIBLE_DEVICES"] == "0"


def test_the_gpu_request_keeps_the_operators_limits():
    container = _container(*GPU_ON, "--set", "flClient.resources.limits.memory=48Gi")

    assert container["resources"]["limits"]["memory"] == "48Gi"
    assert container["resources"]["limits"]["nvidia.com/gpu"] == "1"
    assert container["resources"]["requests"]["cpu"] == "4"


def test_runtime_allocation_keeps_the_node_runtime_shape():
    container = _container(*GPU_ON, "--set", "flClient.gpu.allocation=runtime")

    assert "nvidia.com/gpu" not in (container["resources"].get("limits") or {})
    assert _env(container)["NVIDIA_VISIBLE_DEVICES"] == "0"
    assert _env(container)["CUDA_VISIBLE_DEVICES"] == "0"


def test_a_cpu_only_client_requests_no_gpu():
    container = _container()

    assert "nvidia.com/gpu" not in (container["resources"].get("limits") or {})
    assert "NVIDIA_VISIBLE_DEVICES" not in _env(container)
    assert "CUDA_VISIBLE_DEVICES" not in _env(container)


def test_an_unknown_allocation_is_refused():
    result = _helm(*GPU_ON, "--set", "flClient.gpu.allocation=gpu0")

    assert result.returncode != 0
    # The schema refuses it first; the template's own check covers a render without schema validation.
    assert "/flClient/gpu/allocation" in result.stderr or "flClient.gpu.allocation" in result.stderr
