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

"""Switch an NVFLARE client kit to one Kubernetes pod per job (FLIP#1428).

Run by the fl-client at container start, before NVFLARE starts
(``python -m flip.nvflare.k8s_job_mode /app/local``), when the trust's Helm chart enables job pods.
The client pod then stays a small, long-lived parent that keeps the site registered, and NVFLARE's
``ClientK8sJobLauncher`` starts each job in its own pod. A job pod asks Kubernetes for the GPUs in the
job's ``resource_spec`` (``nvidia.com/gpu``), so a GPU node pool can scale from zero and a GPU is held
only while a job runs.

Three files in ``local/`` change:

- ``resources.json``: the process launcher becomes the K8s launcher. The resource manager declares the
  site's GPU pool (``FL_K8S_MAX_GPUS``) with ``ignore_host`` set, because the parent pod has no GPU to
  probe. A declared pool, rather than NVFLARE's always-yes ``PassthroughResourceManager``, keeps
  NVFLARE's queueing: a job asking for more GPUs than the pool holds waits in the scheduler, and FLIP's
  scheduling reporter tells the hub. The kit's GPU resource consumer becomes NVFLARE's passthrough one:
  NVFLARE will not start a job without a consumer once GPUs are allocated, and the GPU consumer would
  probe the parent's (absent) GPUs and set ``CUDA_VISIBLE_DEVICES`` in the parent's own process. The job
  pod gets its GPU from its ``nvidia.com/gpu`` request.
- ``comm_config.json``: job pods reach the parent through its Service (``FL_K8S_PARENT_SERVICE``).
- ``study_runtime.yaml``: the default study, which every FLIP job lands in (FLIP jobs carry no study),
  gets the job image and the site's pod template. The template is where the chart puts what a job
  pod needs from the trust: its labels, the images volume, the trust-internal key and the FL server's
  host aliases. It is copied into ``local/pod_specs/``, the path NVFLARE resolves it from.

NVFLARE hands each job pod the parent's workspace path, and the launcher mounts the job's (empty)
workspace at that same path. The parent's workspace therefore cannot be a directory the job image needs:
FLIP's process-mode client uses ``/app``, which is also where the image keeps its code and virtualenv, so
the chart gives the parent its own workspace (``/var/tmp/nvflare/workspace``) in this mode, and the switch
refuses a workspace that holds the interpreter it is running under.

Stdlib-only, like ``site_policy``: it runs before NVFLARE and must not fail on a framework import.
``study_runtime.yaml`` is written as JSON, which is valid YAML. Any invalid setting or missing file
fails the container without writing anything: a half-switched kit would start a process-mode client
that runs every job on the parent pod's CPU.
"""

import argparse
import copy
import json
import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LAUNCHER_ID = "k8s_launcher"
LAUNCHER_PATH = "nvflare.app_opt.job_launcher.k8s_launcher.ClientK8sJobLauncher"
GPU_RESOURCE_MANAGER = "nvflare.app_common.resource_managers.gpu_resource_manager.GPUResourceManager"
PASSTHROUGH_RESOURCE_CONSUMER = (
    "nvflare.app_common.resource_consumers.passthrough_resource_consumer.PassthroughResourceConsumer"
)
# Every launcher id NVFLARE knows, so a kit prepared for another runtime cannot keep a second launcher.
_REPLACED_COMPONENT_IDS = {
    "process_launcher",
    "docker_launcher",
    "k8s_launcher",
    "slurm_launcher",
    "resource_consumer",
    "resource_manager",
}
DEFAULT_PARENT_PORT = 8102
DEFAULT_PENDING_TIMEOUT = 900
DEFAULT_MEMORY_PER_GPU_IN_GIB = 16
DEFAULT_EXPIRATION_PERIOD = 300
POD_TEMPLATE = "pod_specs/job-pod.yaml"
STUDY_RUNTIME = "study_runtime.yaml"


@dataclass(frozen=True)
class K8sJobMode:
    """What the switch needs to know about the cluster and the site's GPU pool."""

    namespace: str
    parent_service: str
    parent_port: int
    job_image: str
    max_gpus: int
    mem_per_gpu_gib: float
    pending_timeout: int
    pod_template: Path | None


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required for Kubernetes job pods")
    return value


def _int(env: Mapping[str, str], name: str, default: int | None = None, minimum: int = 0) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        if default is None:
            raise ValueError(f"{name} is required for Kubernetes job pods")
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {raw!r}") from None
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")
    return value


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    return value


def config_from_env(env: Mapping[str, str]) -> K8sJobMode:
    """Read the switch's settings from the environment the chart gives the fl-client.

    Raises:
        ValueError: naming the first missing or invalid setting.
    """
    template = env.get("FL_K8S_POD_TEMPLATE", "").strip()
    return K8sJobMode(
        namespace=_required(env, "FL_K8S_NAMESPACE"),
        parent_service=_required(env, "FL_K8S_PARENT_SERVICE"),
        parent_port=_int(env, "FL_K8S_PARENT_PORT", DEFAULT_PARENT_PORT, minimum=1),
        job_image=_required(env, "FL_K8S_JOB_IMAGE"),
        max_gpus=_int(env, "FL_K8S_MAX_GPUS"),
        mem_per_gpu_gib=_float(env, "MEMORY_PER_GPU_IN_GIB", DEFAULT_MEMORY_PER_GPU_IN_GIB),
        pending_timeout=_int(env, "FL_K8S_PENDING_TIMEOUT", DEFAULT_PENDING_TIMEOUT),
        pod_template=Path(template) if template else None,
    )


def patch_resources(resources: Mapping[str, Any], mode: K8sJobMode, workspace: str) -> dict[str, Any]:
    """Return ``resources.json`` with the K8s launcher and a declared GPU pool; the input is not changed.

    Args:
        resources: The kit's ``resources.json``.
        mode: The cluster and GPU pool settings.
        workspace: The parent's workspace root, where job pods mount theirs.
    """
    patched = copy.deepcopy(dict(resources))
    components = patched.get("components") or []
    previous_manager: dict[str, Any] = next((c for c in components if c.get("id") == "resource_manager"), {})
    expiration = (previous_manager.get("args") or {}).get("expiration_period", DEFAULT_EXPIRATION_PERIOD)
    kept = [c for c in components if c.get("id") not in _REPLACED_COMPONENT_IDS]
    kept.append(
        {
            "id": "resource_manager",
            "path": GPU_RESOURCE_MANAGER,
            "args": {
                "num_of_gpus": mode.max_gpus,
                "mem_per_gpu_in_GiB": mode.mem_per_gpu_gib,
                "expiration_period": expiration,
                "ignore_host": True,
            },
        }
    )
    kept.append({"id": "resource_consumer", "path": PASSTHROUGH_RESOURCE_CONSUMER, "args": {}})
    kept.append(
        {
            "id": LAUNCHER_ID,
            "path": LAUNCHER_PATH,
            "args": {
                # In-cluster credentials: the parent's ServiceAccount, which the chart binds to the launcher Role.
                "config_file_path": None,
                "namespace": mode.namespace,
                "pending_timeout": mode.pending_timeout,
                "default_python_path": sys.executable,
                "workspace_mount_path": workspace,
            },
        }
    )
    patched["components"] = kept
    return patched


def patch_comm_config(comm_config: Mapping[str, Any], mode: K8sJobMode) -> dict[str, Any]:
    """Return ``comm_config.json`` pointing job pods at the parent's Service, keeping every other key."""
    patched = copy.deepcopy(dict(comm_config))
    patched["internal"] = {
        "scheme": "tcp",
        "resources": {"host": mode.parent_service, "port": mode.parent_port, "connection_security": "mtls"},
    }
    return patched


def study_runtime(mode: K8sJobMode) -> dict[str, Any]:
    """Return ``study_runtime.yaml``: the default study's job image and, when the site has one, its pod template."""
    default: dict[str, Any] = {"container": {"image": mode.job_image}}
    if mode.pod_template is not None:
        default["pod_template"] = POD_TEMPLATE
    return {"format_version": 2, "studies": {"default": default}}


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return data


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def apply(local_dir: Path, mode: K8sJobMode) -> None:
    """Rewrite the kit's ``local/`` for job pods. Everything is read and checked before anything is written.

    Raises:
        OSError: a file is missing or cannot be written.
        ValueError: a file is not valid JSON.
    """
    workspace = local_dir.resolve().parent
    if Path(sys.executable).resolve().is_relative_to(workspace) or Path(sys.executable).is_relative_to(workspace):
        raise ValueError(
            f"job pods mount their workspace at the parent's, {workspace}, which would hide this image's Python "
            f"({sys.executable}); give the client a workspace of its own, e.g. /var/tmp/nvflare/workspace"
        )
    resources_path = local_dir / "resources.json"
    source = resources_path if resources_path.exists() else local_dir / "resources.json.default"
    if not source.exists():
        raise FileNotFoundError(f"no resources.json or resources.json.default in {local_dir}")
    resources = patch_resources(_read_json(source), mode, str(workspace))
    comm_path = local_dir / "comm_config.json"
    comm_config = patch_comm_config(_read_json(comm_path) if comm_path.exists() else {}, mode)
    if mode.pod_template is not None and not mode.pod_template.is_file():
        raise FileNotFoundError(f"pod template {mode.pod_template} does not exist")

    if mode.pod_template is not None:
        (local_dir / POD_TEMPLATE).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(mode.pod_template, local_dir / POD_TEMPLATE)
    _write_json(local_dir / STUDY_RUNTIME, study_runtime(mode))
    _write_json(comm_path, comm_config)
    _write_json(resources_path, resources)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """CLI entry point: ``python -m flip.nvflare.k8s_job_mode <local_dir>``.

    Returns:
        ``0`` once the kit is switched, ``1`` on an invalid setting or a file problem. Callers must treat ``1`` as
        fatal (fail closed).
    """
    parser = argparse.ArgumentParser(prog="flip.nvflare.k8s_job_mode", description=__doc__)
    parser.add_argument("local_dir", type=Path, help="The client kit's local/ directory (e.g. /app/local)")
    parsed = parser.parse_args(argv)
    try:
        mode = config_from_env(os.environ if env is None else env)
        apply(parsed.local_dir, mode)
    except (OSError, ValueError) as e:
        print(f"❌ cannot switch the FL client to Kubernetes job pods: {e}", file=sys.stderr)
        return 1
    template = f", pod template {POD_TEMPLATE}" if mode.pod_template else ""
    print(
        f"✅ FL client runs each job in its own pod: image {mode.job_image}, namespace {mode.namespace}, "
        f"up to {mode.max_gpus} GPU(s) at once{template}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
