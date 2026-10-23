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

"""The NVFLARE client tells its scheduler the GPUs this pod has (FLIP#1509).

Provisioning ships each client kit a ``local/resources.template.json`` with the GPU count left as
``${NUM_AVAILABLE_GPUS}``. The Compose client renders it at start; the chart used to copy
``resources.json.default`` instead, which holds whatever provisioning wrote, so a job that declares a GPU could not
schedule on a Kubernetes trust. These tests render the chart, lift the client's resources step out of its command and
run it against a scratch ``local/`` directory. Skipped without helm; the chart workflow's helm-template job runs it.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
KIT_HOST_PATH = "/opt/flip/fl-kit"
SENTINEL = "past-the-resources-step"
TEMPLATE = (
    '{"components": [{"id": "resource_manager", "path": "x", "args": '
    '{"num_of_gpus": ${NUM_AVAILABLE_GPUS}, "mem_per_gpu_in_GiB": ${MEMORY_PER_GPU_IN_GIB}, '
    '"note": "costs $5 a day"}}]}'
)

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
needs_envsubst = pytest.mark.skipif(shutil.which("envsubst") is None, reason="envsubst is not installed")


def _helm(*extra: str) -> subprocess.CompletedProcess:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", f"flClient.kitHostPath={KIT_HOST_PATH}"]
    return subprocess.run([*args, *extra], capture_output=True, text=True, timeout=120)


def _render(*extra: str) -> str:
    result = _helm(*extra)
    assert result.returncode == 0, f"helm template failed: {result.stderr}"
    return result.stdout


def _client_command(rendered: str) -> str:
    for doc in yaml.safe_load_all(rendered):
        if (
            doc
            and doc.get("kind") == "Deployment"
            and doc["metadata"]["labels"].get("app.kubernetes.io/component") == "fl-client"
        ):
            container = next(c for c in doc["spec"]["template"]["spec"]["containers"] if c["name"] == "fl-client")
            return "\n".join(container.get("command", []) + container.get("args", []))
    raise AssertionError("no fl-client Deployment in the render")


def _resources_step(command: str) -> str:
    match = re.search(r"if \[ -f /app/local/resources\.template\.json \]; then\n.*?\n\s*fi\n", command, flags=re.S)
    assert match, "the NVFLARE client no longer renders resources.json from the kit's template"
    return match.group(0)


def _configmap(rendered: str) -> dict:
    return next(
        doc
        for doc in yaml.safe_load_all(rendered)
        if doc and doc.get("kind") == "ConfigMap" and doc["metadata"]["name"].endswith("-fl-client")
    )


def _run_step(local: Path, gpus: str) -> subprocess.CompletedProcess:
    """Run the step under plain bash, then a sentinel: a failure must stop the client without help from set -e."""
    script = _resources_step(_client_command(_render())).replace("/app/local", str(local)) + f"echo {SENTINEL}\n"
    env = {"PATH": os.environ["PATH"], "NUM_AVAILABLE_GPUS": gpus, "MEMORY_PER_GPU_IN_GIB": "7"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)


def _assert_stops(result: subprocess.CompletedProcess, message: str) -> None:
    assert result.returncode != 0
    assert SENTINEL not in result.stdout
    assert message in result.stderr


@needs_envsubst
@pytest.mark.parametrize(("gpus", "expected"), [("0", 0), ("1", 1)])
def test_the_client_offers_the_pods_gpus(tmp_path, gpus, expected):
    # A CPU-only node (count 0) renders 0, so NVFLARE never probes for a GPU it lacks.
    (tmp_path / "resources.template.json").write_text(TEMPLATE)

    assert _run_step(tmp_path, gpus).returncode == 0

    args = json.loads((tmp_path / "resources.json").read_text())["components"][0]["args"]
    assert args["num_of_gpus"] == expected
    assert args["mem_per_gpu_in_GiB"] == 7


@needs_envsubst
def test_only_the_gpu_variables_are_substituted(tmp_path):
    # Any other "$" in the provisioned file survives.
    (tmp_path / "resources.template.json").write_text(TEMPLATE)

    assert _run_step(tmp_path, "1").returncode == 0

    args = json.loads((tmp_path / "resources.json").read_text())["components"][0]["args"]
    assert args["note"] == "costs $5 a day"


def test_a_kit_without_the_template_keeps_the_shipped_default(tmp_path):
    # Kits staged before restructure-lib.sh wrote the template still start.
    (tmp_path / "resources.json.default").write_text('{"shipped": true}')

    assert _run_step(tmp_path, "1").returncode == 0

    assert json.loads((tmp_path / "resources.json").read_text()) == {"shipped": True}


@needs_envsubst
@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to a read-only directory")
def test_an_unwritable_local_dir_stops_the_client(tmp_path):
    # Fail closed rather than run with the provisioned count, with the message the upgrade runbook names.
    (tmp_path / "resources.template.json").write_text(TEMPLATE)
    tmp_path.chmod(0o555)
    try:
        _assert_stops(_run_step(tmp_path, "1"), "cannot write")
    finally:
        tmp_path.chmod(0o755)
    assert "cannot write /app/local/resources.json" in _client_command(_render())


def test_a_kit_with_neither_file_stops_the_client(tmp_path):
    _assert_stops(_run_step(tmp_path, "1"), "resources.json.default")


def test_the_flower_client_command_is_unchanged_by_this():
    command = _client_command(_render("--set", "flBackend=flower"))

    assert "resources.template.json" not in command
    assert "envsubst" not in command


def test_the_pods_gpu_count_reaches_the_client():
    rendered = _render("--set", "flClient.gpu.enabled=true", "--set", "flClient.gpu.count=1")

    assert _configmap(rendered)["data"]["NUM_AVAILABLE_GPUS"] == "1"


def test_a_pod_without_a_gpu_offers_none():
    # Turning the GPU off while leaving a count set must not make NVFLARE probe for one.
    rendered = _render("--set", "flClient.gpu.enabled=false", "--set", "flClient.gpu.count=1")

    assert _configmap(rendered)["data"]["NUM_AVAILABLE_GPUS"] == "0"


@pytest.mark.parametrize("count", [-1, 0, 2])
def test_enabled_gpu_requires_one_gpu_at_render(count):
    # Zero silently offers no GPU; counts above one exceed the pod's GPU 0 visibility.
    result = _helm("--set", "flClient.gpu.enabled=true", "--set", f"flClient.gpu.count={count}")

    assert result.returncode != 0
    assert "flClient.gpu.count" in result.stderr


@pytest.mark.parametrize("memory", [7, 24])
def test_gpu_memory_reaches_the_client(memory):
    rendered = _render("--set", f"flClient.gpu.memoryPerGpuInGiB={memory}")
    assert _configmap(rendered)["data"]["MEMORY_PER_GPU_IN_GIB"] == str(memory)


def _run_log_step(local: Path) -> subprocess.CompletedProcess:
    rendered = _render("--set", "logLevel=DEBUG")
    deployment = next(
        doc
        for doc in yaml.safe_load_all(rendered)
        if doc
        and doc.get("kind") == "Deployment"
        and doc["metadata"]["labels"].get("app.kubernetes.io/component") == "fl-client"
    )
    container = next(c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "fl-client")
    level = next(e["value"] for e in container["env"] if e["name"] == "LOG_LEVEL")
    match = re.search(
        r"if \[ -f /app/local/log_config\.template\.json \]; then\n.*?\n\s*fi\n", _client_command(rendered), re.S
    )
    assert match, "the NVFLARE client must render the log template"
    script = match.group(0).replace("/app/local", str(local)) + f"echo {SENTINEL}\n"
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "LOG_LEVEL": level},
        timeout=30,
    )


@needs_envsubst
def test_log_level_renders_without_substituting_other_variables(tmp_path):
    (tmp_path / "log_config.template.json").write_text('{"level": "${LOG_LEVEL}", "note": "$OTHER"}')
    assert _run_log_step(tmp_path).returncode == 0
    assert json.loads((tmp_path / "log_config.json").read_text()) == {"level": "DEBUG", "note": "$OTHER"}


def test_log_config_falls_back_to_the_shipped_default(tmp_path):
    (tmp_path / "log_config.json.default").write_text('{"level": "INFO"}')
    assert _run_log_step(tmp_path).returncode == 0
    assert json.loads((tmp_path / "log_config.json").read_text()) == {"level": "INFO"}


def test_missing_log_config_stops_the_client(tmp_path):
    _assert_stops(_run_log_step(tmp_path), "log_config.json.default")


@needs_envsubst
@pytest.mark.parametrize("source", ["log_config.template.json", "log_config.json.default"])
def test_unwritable_log_config_stops_the_client(tmp_path, source):
    (tmp_path / source).write_text('{"level": "INFO"}')
    # A non-directory parent behind the output symlink makes both writes fail, including as root.
    (tmp_path / "log_config.json").symlink_to(tmp_path / "blocked" / "config.json")
    (tmp_path / "blocked").write_text("not a directory")
    _assert_stops(_run_log_step(tmp_path), "cannot")


def test_enabling_gpu_without_setting_count_is_refused():
    result = _helm("--set", "flClient.gpu.enabled=true")
    assert result.returncode != 0
    assert "flClient.gpu.count must be 1" in result.stderr


@pytest.mark.parametrize("count", [0, 2])
def test_nvflare_count_guard_does_not_restrict_flower(count):
    _render("--set", "flBackend=flower", "--set", "flClient.gpu.enabled=true", "--set", f"flClient.gpu.count={count}")
