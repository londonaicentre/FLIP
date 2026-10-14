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
Container Service's pods reserve nothing, and a BestEffort-scoped ResourceQuota caps how many run
at once. The cap counts every BestEffort pod in the namespace, so it holds only while no pod of
the chart is BestEffort itself: one that were would compete with the conversions for the cap and
could wait behind a backlog of them. Asserted on `helm template` output. Skipped without helm.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = CHART_DIR.parents[2]
COMMAND_FILE = REPO_ROOT / "trust" / "xnat" / "xnat" / "config" / "dcm2niix_command.json"
KIT_PATH = "flClient.kitHostPath=/opt/flip/fl-kit"
POD_KINDS = ("Deployment", "StatefulSet", "DaemonSet", "Job")
# The command keys the Container Service turns into pod requests and limits.
RESOURCE_KEY = re.compile(r"^(reserve|limit)-")

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


def _init_job_parts() -> tuple[str, dict]:
    """The configure-dcm2niix script and the command JSON the xnat-init ConfigMap carries."""
    docs = _docs("--set", KIT_PATH, "--show-only", "templates/xnat-init-job.yaml")
    job = next(d for d in docs if d["kind"] == "Job")
    container = next(c for c in job["spec"]["template"]["spec"]["containers"] if c["name"] == "configure-dcm2niix")
    configmap = next(d for d in docs if d["kind"] == "ConfigMap")
    return container["command"][-1], json.loads(configmap["data"]["dcm2niix_command.json"])


def test_the_configmap_carries_the_command_file_unchanged():
    """The chart cannot read the file (it lives outside the chart), so it carries a copy. The
    Kubernetes difference is made at registration, below, and nowhere else."""
    _, command = _init_job_parts()
    assert command == json.loads(COMMAND_FILE.read_text())


def test_dcm2niix_is_registered_without_any_resource_setting():
    """Run the script's own jq filter over the command it registers: whatever resource keys the
    command file gains, none may reach the Kubernetes registration."""
    if shutil.which("jq") is None:
        pytest.skip("jq is not installed")
    script, command = _init_job_parts()
    assert any(RESOURCE_KEY.match(key) for key in command), "premise: the command file sets resources"

    found = re.search(r"jq '([^']+)' \\\s*/cs-config/dcm2niix_command\.json > (\S+)", script)
    assert found, "configure-dcm2niix no longer filters the command before registering it"
    expression, stripped_path = found.groups()
    stripped = json.loads(
        subprocess.run(
            ["jq", expression], input=json.dumps(command), capture_output=True, text=True, check=True, timeout=30
        ).stdout
    )
    assert not [key for key in stripped if RESOURCE_KEY.match(key)], stripped
    assert {k: v for k, v in command.items() if not RESOURCE_KEY.match(k)} == stripped

    registration = re.search(r'-X POST "\$\{XNAT_URL\}/xapi/commands" \\(?:\s*[^\n]*\\)*\s*-d @(\S+)', script)
    assert registration, "the command registration call moved; update this test"
    assert registration.group(1) == stripped_path, "the registration must post the filtered command"
