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

"""The NVFLARE client tells its scheduler the GPUs this pod has (FLIP#1509), from a kit it cannot write (FLIP#1570).

Provisioning ships each client kit a ``local/resources.template.json`` with the GPU count left as
``${NUM_AVAILABLE_GPUS}``. The chart used to copy ``resources.json.default`` instead, which holds whatever provisioning
wrote, so a job that declares a GPU could not schedule on a Kubernetes trust. The render is now the fl-client-init
init container's (``/app/fl-client-init.sh``, shared with Compose), and the client mounts ``local/`` read-only. These
tests render the chart to pin that wiring, and run the init script against a scratch kit with ``python`` stubbed out
(the site policy has its own tests in flip-utils). Skipped without helm; the chart workflow's helm-template job runs it.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
INIT_SCRIPT = CHART_DIR.parents[2] / "fl-services" / "nvflare" / "fl-client" / "fl-client" / "init.sh"
KIT_HOST_PATH = "/opt/flip/fl-kit"
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


def _pod(rendered: str) -> dict:
    for doc in yaml.safe_load_all(rendered):
        if (
            doc
            and doc.get("kind") == "Deployment"
            and doc["metadata"]["labels"].get("app.kubernetes.io/component") == "fl-client"
        ):
            return doc["spec"]["template"]["spec"]
    raise AssertionError("no fl-client Deployment in the render")


def _client(rendered: str) -> dict:
    return next(c for c in _pod(rendered)["containers"] if c["name"] == "fl-client")


def _init(rendered: str) -> dict:
    return next(c for c in _pod(rendered)["initContainers"] if c["name"] == "fl-client-init")


def _client_command(rendered: str) -> str:
    container = _client(rendered)
    return "\n".join(container.get("command", []) + container.get("args", []))


def _configmap(rendered: str) -> dict:
    return next(
        doc
        for doc in yaml.safe_load_all(rendered)
        if doc and doc.get("kind") == "ConfigMap" and doc["metadata"]["name"].endswith("-fl-client")
    )


def _run_init(tmp_path: Path, **env: str) -> subprocess.CompletedProcess:
    """Run the init script over a kit at tmp_path/kit, with python (the site policy step) stubbed to succeed."""
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "python").write_text("#!/bin/sh\nexit 0\n")
    (stub / "python").chmod(0o755)
    base = {"PATH": f"{stub}:{os.environ['PATH']}", "NUM_AVAILABLE_GPUS": "1", "MEMORY_PER_GPU_IN_GIB": "7"}
    return subprocess.run(
        ["bash", str(INIT_SCRIPT), str(tmp_path / "kit")],
        capture_output=True,
        text=True,
        env={**base, **env},
        timeout=30,
    )


@pytest.fixture
def local(tmp_path) -> Path:
    for sub in ("local", "startup"):
        (tmp_path / "kit" / sub).mkdir(parents=True)
    (tmp_path / "kit" / "local" / "log_config.json.default").write_text('{"level": "INFO"}')
    return tmp_path / "kit" / "local"


def test_the_init_container_renders_the_kit_before_the_client_starts():
    rendered = _render("--set", "logLevel=DEBUG")
    init = _init(rendered)

    assert init["image"] == _client(rendered)["image"], "the render must come from the image the client runs"
    assert "/app/fl-client-init.sh /app" in init["command"][-1]
    assert init["envFrom"] == _client(rendered)["envFrom"], "the GPU count and memory come from the fl-client ConfigMap"
    assert {"name": "LOG_LEVEL", "value": "DEBUG"} in init["env"]
    assert {"name": "LOG_LEVEL", "value": "DEBUG"} in _client(rendered)["env"], "the running client keeps LOG_LEVEL"
    assert init["securityContext"]["runAsNonRoot"] is True, "the init writes the kit, so non-root must be enforced"
    assert init["securityContext"]["runAsUser"] == 1000, "the client's user, so the client can read what it wrote"
    dockerfile = (INIT_SCRIPT.parents[1] / "Dockerfile").read_text()
    assert "fl-client/init.sh /app/fl-client-init.sh" in dockerfile, "the image no longer ships the script"


@pytest.mark.parametrize("job_pods", [False, True])
@pytest.mark.parametrize("source", ["hostPath", "secret"])
def test_the_client_mounts_its_kit_read_only_and_writes_nothing_into_it(source, job_pods):
    rendered = _render(
        "--set", f"flClient.kit.source={source}", "--set", f"flClient.nvflare.jobPods.enabled={str(job_pods).lower()}"
    )
    ws = "/var/tmp/nvflare/workspace" if job_pods else "/app"
    client = _client(rendered)
    mounts = {m["mountPath"]: m for m in client["volumeMounts"]}
    init_mounts = {m["mountPath"]: m for m in _init(rendered)["volumeMounts"]}

    for sub in ("local", "startup"):
        assert mounts[f"{ws}/{sub}"].get("readOnly") is True, f"the client can write its kit's {sub}/"
        assert init_mounts[f"{ws}/{sub}"]["subPath"] == sub
        assert not init_mounts[f"{ws}/{sub}"].get("readOnly"), f"fl-client-init must be able to write {sub}/"
    assert not mounts[f"{ws}/transfer"].get("readOnly"), "NVFLARE stages job payloads through transfer/"
    command = _client_command(rendered)
    for write in ("envsubst", "chmod", f"> {ws}/local", "cp ", "k8s_job_mode"):
        assert write not in command, f"the client still writes its kit ({write!r})"
    assert f"python -m flip.nvflare.site_policy --assert-rendered {ws}/local/privacy.json" in command
    init_names = [c["name"] for c in _pod(rendered)["initContainers"]]
    assert init_names[-1] == "fl-client-init", "fl-client-init must run after the kit is staged and the extract written"


@needs_envsubst
@pytest.mark.parametrize(("gpus", "expected"), [("0", 0), ("1", 1)])
def test_the_client_offers_the_pods_gpus(tmp_path, local, gpus, expected):
    # A CPU-only node (count 0) renders 0, so NVFLARE never probes for a GPU it lacks.
    (local / "resources.template.json").write_text(TEMPLATE)

    assert _run_init(tmp_path, NUM_AVAILABLE_GPUS=gpus).returncode == 0

    args = json.loads((local / "resources.json").read_text())["components"][0]["args"]
    assert args["num_of_gpus"] == expected
    assert args["mem_per_gpu_in_GiB"] == 7


@needs_envsubst
def test_only_the_gpu_variables_are_substituted(tmp_path, local):
    # Any other "$" in the provisioned file survives.
    (local / "resources.template.json").write_text(TEMPLATE)

    assert _run_init(tmp_path).returncode == 0

    args = json.loads((local / "resources.json").read_text())["components"][0]["args"]
    assert args["note"] == "costs $5 a day"


def test_a_kit_without_the_template_keeps_the_shipped_default(tmp_path, local):
    # Kits staged before restructure-lib.sh wrote the template still start.
    (local / "resources.json.default").write_text('{"shipped": true}')

    result = _run_init(tmp_path)
    assert result.returncode == 0

    assert json.loads((local / "resources.json").read_text()) == {"shipped": True}
    # The GPU values cannot reach the shipped default, and the log must not claim they did.
    assert "NOT applied" in result.stderr
    assert "NUM_AVAILABLE_GPUS=1" not in result.stdout


def test_the_shipped_default_needs_no_warning_when_no_gpu_values_are_set(tmp_path, local):
    (local / "resources.json.default").write_text("{}")
    result = _run_init(tmp_path, NUM_AVAILABLE_GPUS="", MEMORY_PER_GPU_IN_GIB="")
    assert result.returncode == 0
    assert "NOT applied" not in result.stderr


@needs_envsubst
@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to a read-only directory")
def test_an_unwritable_local_dir_stops_the_client(tmp_path, local):
    # Fail closed rather than run with the provisioned count.
    (local / "resources.template.json").write_text(TEMPLATE)
    local.chmod(0o555)
    try:
        result = _run_init(tmp_path)
    finally:
        local.chmod(0o755)
    assert result.returncode != 0
    # The message the upgrade runbook names (admin-upgrading-sites.rst).
    assert f"cannot write {local}" in result.stderr
    assert not (local / "resources.json").exists()


def test_a_kit_with_neither_file_stops_the_client(tmp_path, local):
    result = _run_init(tmp_path)
    assert result.returncode != 0
    assert "resources.json.default" in result.stderr


def test_a_failed_render_leaves_no_stale_render_for_the_client(tmp_path, local):
    # The client starts only on files present, so a failure must not leave the previous run's.
    (local / "resources.json").write_text('{"stale": true}')
    (local / "log_config.json").write_text('{"stale": true}')
    assert _run_init(tmp_path).returncode != 0
    assert not (local / "resources.json").exists()
    assert not (local / "log_config.json").exists()


@needs_envsubst
def test_a_read_only_rendered_file_is_replaced(tmp_path, local):
    # A kit staged from a Secret arrives 0400 in a directory the client user owns.
    (local / "resources.template.json").write_text(TEMPLATE)
    (local / "resources.json").write_text("{}")
    (local / "resources.json").chmod(0o400)

    assert _run_init(tmp_path).returncode == 0
    assert json.loads((local / "resources.json").read_text())["components"][0]["args"]["num_of_gpus"] == 1


def test_the_flower_client_command_is_unchanged_by_this():
    rendered = _render("--set", "flBackend=flower")
    command = _client_command(rendered)

    assert "resources.template.json" not in command
    assert "envsubst" not in command
    assert "fl-client-init" not in [c["name"] for c in _pod(rendered)["initContainers"]]


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


@needs_envsubst
def test_log_level_renders_without_substituting_other_variables(tmp_path, local):
    (local / "resources.json.default").write_text("{}")
    (local / "log_config.template.json").write_text('{"level": "${LOG_LEVEL}", "note": "$OTHER"}')
    assert _run_init(tmp_path, LOG_LEVEL="DEBUG").returncode == 0
    assert json.loads((local / "log_config.json").read_text()) == {"level": "DEBUG", "note": "$OTHER"}


def test_log_config_falls_back_to_the_shipped_default(tmp_path, local):
    (local / "resources.json.default").write_text("{}")
    assert _run_init(tmp_path).returncode == 0
    assert json.loads((local / "log_config.json").read_text()) == {"level": "INFO"}


def test_missing_log_config_stops_the_client(tmp_path, local):
    (local / "resources.json.default").write_text("{}")
    (local / "log_config.json.default").unlink()
    result = _run_init(tmp_path)
    assert result.returncode != 0
    assert "log_config.json.default" in result.stderr


@needs_envsubst
@pytest.mark.parametrize("source", ["log_config.template.json", "log_config.json.default"])
def test_unwritable_log_config_stops_the_client(tmp_path, local, source):
    (local / "resources.json.default").write_text("{}")
    (local / source).write_text('{"level": "INFO"}')
    # A non-directory parent behind the output's temporary file makes both writes fail, including as root.
    (local / "log_config.json.tmp").symlink_to(local / "blocked" / "config.json")
    (local / "blocked").write_text("not a directory")
    assert _run_init(tmp_path).returncode != 0


def test_enabling_gpu_without_setting_count_is_refused():
    result = _helm("--set", "flClient.gpu.enabled=true")
    assert result.returncode != 0
    assert "flClient.gpu.count must be 1" in result.stderr


@pytest.mark.parametrize("count", [0, 2])
def test_nvflare_count_guard_does_not_restrict_flower(count):
    _render("--set", "flBackend=flower", "--set", "flClient.gpu.enabled=true", "--set", f"flClient.gpu.count={count}")
