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

"""dcm2niix on Kubernetes runs as BestEffort pods, at most N at a time (FLIP#1448), rendered.

The xnat-init Job registers dcm2niix without the command file's resource settings, so the
Container Service's pods reserve nothing (configure-dcm2niix.sh drops them on the kubernetes
backend, trust/xnat/tests/test_dcm2niix_registration.py), and a BestEffort-scoped ResourceQuota caps
how many run at once. The cap counts every BestEffort pod in the namespace, so it holds only while no pod of
the chart is BestEffort itself: one that were would compete with the conversions for the cap and
could wait behind a backlog of them. Asserted on `helm template` output. Skipped without helm.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
KIT_PATH = "flClient.kitHostPath=/opt/flip/fl-kit"
POD_KINDS = ("Deployment", "StatefulSet", "DaemonSet", "Job")

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _docs(*args: str) -> list[dict]:
    cmd = ["helm", "template", "trust-release", str(CHART_DIR), *args]
    rendered = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120).stdout
    return [d for d in yaml.safe_load_all(rendered) if d]


def _quotas(*sets: str) -> list[dict]:
    args = ["--set", KIT_PATH]
    for item in sets:
        args += ["--set", item]
    return [d for d in _docs(*args) if d["kind"] == "ResourceQuota"]


def test_the_quota_admits_the_configured_number_of_best_effort_pods():
    (quota,) = _quotas()
    assert quota["spec"]["scopes"] == ["BestEffort"]
    assert quota["spec"]["hard"] == {"pods": "4"}
    (quota,) = _quotas("xnat.containerService.maxConcurrentJobs=2")
    assert quota["spec"]["hard"] == {"pods": "2"}


@pytest.mark.parametrize("unset", ["xnat.containerService.maxConcurrentJobs=null", "xnat.enabled=false"])
def test_no_quota_without_a_cap_or_without_xnat(unset):
    assert _quotas(unset) == []


RENDERS = {
    "default": ["--set", KIT_PATH],
    "flower": ["--set", KIT_PATH, "--set", "flBackend=flower"],
    "aks small node": ["-f", str(CHART_DIR / "values-aks.yaml"), "-f", str(CHART_DIR / "values-small-node.yaml")],
    "vocab load and gpu": [
        "--set",
        KIT_PATH,
        "--set",
        "omopDb.vocabLoad.enabled=true",
        "--set",
        "omopDb.vocabLoad.s3Bucket=bucket",
        "--set",
        "flClient.gpu.enabled=true",
        "--set",
        "flClient.gpu.operator.enabled=true",
    ],
}


@pytest.mark.parametrize("render", RENDERS)
def test_no_pod_of_the_chart_is_best_effort(render):
    """A pod is BestEffort only if none of its containers, init containers included, sets a
    request or a limit; one that sets any is outside the quota's scope."""
    best_effort = []
    for doc in _docs(*RENDERS[render]):
        if doc["kind"] not in POD_KINDS:
            continue
        spec = doc["spec"]["template"]["spec"]
        containers = spec.get("initContainers", []) + spec["containers"]
        if not any(c.get("resources", {}).get("requests") or c.get("resources", {}).get("limits") for c in containers):
            best_effort.append(f"{doc['kind']}/{doc['metadata']['name']}")
    assert not best_effort, f"BestEffort pods would count against the conversion cap: {best_effort}"
