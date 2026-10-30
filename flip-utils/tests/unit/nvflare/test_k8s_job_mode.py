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

"""Unit tests for ``flip.nvflare.k8s_job_mode``: the fl-client's switch to one Kubernetes pod per job (FLIP#1428)."""

import ast
import json
import sys
from pathlib import Path

import pytest

from flip.nvflare import k8s_job_mode
from flip.nvflare.k8s_job_mode import (
    GPU_RESOURCE_MANAGER,
    LAUNCHER_PATH,
    PASSTHROUGH_RESOURCE_CONSUMER,
    config_from_env,
    main,
    patch_comm_config,
    patch_resources,
    study_runtime,
)

WORKSPACE = "/var/tmp/nvflare/workspace"
ENV = {
    "FL_K8S_NAMESPACE": "flip-trust",
    "FL_K8S_PARENT_SERVICE": "flip-trust-fl-client-net-1-parent",
    "FL_K8S_JOB_IMAGE": "ghcr.io/londonaicentre/flare-fl-client:stag",
    "FL_K8S_MAX_GPUS": "2",
    "MEMORY_PER_GPU_IN_GIB": "7",
}

# What a FLIP kit's resources.json.default carries: the provisioned process-mode trio plus FLIP's own components.
KIT_RESOURCES = {
    "format_version": 2,
    "client": {"retry_timeout": 30},
    "components": [
        {
            "id": "resource_manager",
            "path": "nvflare.app_common.resource_managers.gpu_resource_manager.GPUResourceManager",
            "args": {"num_of_gpus": 1, "mem_per_gpu_in_GiB": 16, "expiration_period": 300},
        },
        {
            "id": "resource_consumer",
            "path": "nvflare.app_common.resource_consumers.gpu_resource_consumer.GPUResourceConsumer",
            "args": {},
        },
        {
            "id": "process_launcher",
            "path": "nvflare.app_common.job_launcher.client_process_launcher.ClientProcessJobLauncher",
            "args": {},
        },
        {"id": "site_log_streamer", "path": "nvflare.app_common.logging.site_log_streamer.SiteLogStreamer"},
    ],
}


def _by_id(resources):
    return {c["id"]: c for c in resources["components"]}


def test_the_process_launcher_is_replaced_by_the_k8s_launcher():
    patched = _by_id(patch_resources(KIT_RESOURCES, config_from_env(ENV), WORKSPACE))

    assert "process_launcher" not in patched
    launcher = patched["k8s_launcher"]
    assert launcher["path"] == LAUNCHER_PATH
    assert launcher["args"]["namespace"] == "flip-trust"
    assert launcher["args"]["pending_timeout"] == 900
    # The FLIP image's interpreter, not NVFLARE's /usr/local/bin/python3 default, which it does not have.
    assert launcher["args"]["default_python_path"] == sys.executable
    # NVFLARE hands the job pod the parent's workspace path (-m), so the job's workspace mounts at that same path.
    assert launcher["args"]["workspace_mount_path"] == WORKSPACE


def test_the_site_declares_its_gpu_pool_without_probing_the_parent_host():
    """The parent pod has no GPU: probing it would refuse every GPU job, and passthrough would never queue one."""
    patched = _by_id(patch_resources(KIT_RESOURCES, config_from_env(ENV), WORKSPACE))

    manager = patched["resource_manager"]
    assert manager["path"] == GPU_RESOURCE_MANAGER
    assert manager["args"]["num_of_gpus"] == 2
    assert manager["args"]["mem_per_gpu_in_GiB"] == 7
    assert manager["args"]["ignore_host"] is True
    assert manager["args"]["expiration_period"] == 300, "the kit's reservation window carries over"


def test_allocated_gpus_are_consumed_by_kubernetes_not_the_parent():
    """NVFLARE refuses to start a job without a consumer once the manager allocates GPUs.

    The kit's GPUResourceConsumer probes the host's GPUs and sets CUDA_VISIBLE_DEVICES in the parent's own
    process, which has no GPU; the job pod gets its GPU from its nvidia.com/gpu request instead.
    """
    consumer = _by_id(patch_resources(KIT_RESOURCES, config_from_env(ENV), WORKSPACE))["resource_consumer"]

    assert consumer == {"id": "resource_consumer", "path": PASSTHROUGH_RESOURCE_CONSUMER, "args": {}}


def test_components_the_switch_does_not_own_are_kept_untouched():
    patched = patch_resources(KIT_RESOURCES, config_from_env(ENV), WORKSPACE)

    assert _by_id(patched)["site_log_streamer"] == _by_id(KIT_RESOURCES)["site_log_streamer"]
    assert patched["client"] == {"retry_timeout": 30}
    assert "process_launcher" in _by_id(KIT_RESOURCES), "the input is not mutated"


def test_patching_twice_gives_the_same_resources():
    mode = config_from_env(ENV)
    once = patch_resources(KIT_RESOURCES, mode, WORKSPACE)

    assert patch_resources(once, mode, WORKSPACE) == once


def test_job_pods_reach_the_parent_through_its_service():
    comm = patch_comm_config({"allow_adhoc_conns": False}, config_from_env(ENV))

    assert comm["allow_adhoc_conns"] is False
    assert comm["internal"] == {
        "scheme": "tcp",
        "resources": {"host": "flip-trust-fl-client-net-1-parent", "port": 8102, "connection_security": "mtls"},
    }


def test_job_pods_default_to_the_client_image_and_the_site_pod_template():
    runtime = study_runtime(config_from_env({**ENV, "FL_K8S_POD_TEMPLATE": "/app/k8s/job-pod.yaml"}))

    assert runtime["format_version"] == 2
    # FLIP jobs carry no study, which NVFLARE resolves to "default".
    assert runtime["studies"]["default"] == {
        "container": {"image": "ghcr.io/londonaicentre/flare-fl-client:stag"},
        "pod_template": "pod_specs/job-pod.yaml",
    }


def test_without_a_pod_template_only_the_image_is_set():
    assert study_runtime(config_from_env(ENV))["studies"]["default"] == {
        "container": {"image": "ghcr.io/londonaicentre/flare-fl-client:stag"}
    }


REQUIRED = ["FL_K8S_NAMESPACE", "FL_K8S_PARENT_SERVICE", "FL_K8S_JOB_IMAGE", "FL_K8S_MAX_GPUS"]


@pytest.mark.parametrize("missing", REQUIRED)
def test_a_missing_required_setting_is_named(missing):
    env = {k: v for k, v in ENV.items() if k != missing}

    with pytest.raises(ValueError, match=missing):
        config_from_env(env)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FL_K8S_MAX_GPUS", "-1"),
        ("FL_K8S_MAX_GPUS", "two"),
        ("FL_K8S_PARENT_PORT", "0"),
        ("FL_K8S_PENDING_TIMEOUT", "-5"),
        ("MEMORY_PER_GPU_IN_GIB", "-1"),
        ("MEMORY_PER_GPU_IN_GIB", "lots"),
    ],
)
def test_an_invalid_number_is_refused(name, value):
    with pytest.raises(ValueError, match=name):
        config_from_env({**ENV, name: value})


def _kit(tmp_path, resources_name="resources.json"):
    local = tmp_path / "local"
    local.mkdir()
    (local / resources_name).write_text(json.dumps(KIT_RESOURCES))
    return local


def test_the_entrypoint_switches_a_staged_kit(tmp_path):
    local = _kit(tmp_path)
    template = tmp_path / "job-pod.yaml"
    template.write_text("apiVersion: v1\nkind: Pod\n")

    assert main([str(local)], env={**ENV, "FL_K8S_POD_TEMPLATE": str(template)}) == 0

    assert _by_id(json.loads((local / "resources.json").read_text()))["k8s_launcher"]["path"] == LAUNCHER_PATH
    assert json.loads((local / "comm_config.json").read_text())["internal"]["resources"]["port"] == 8102
    runtime = json.loads((local / "study_runtime.yaml").read_text())
    assert runtime["studies"]["default"]["pod_template"] == "pod_specs/job-pod.yaml"
    assert (local / "pod_specs" / "job-pod.yaml").read_text() == template.read_text()


def test_the_entrypoint_reads_the_kit_default_when_nothing_was_rendered(tmp_path):
    local = _kit(tmp_path, resources_name="resources.json.default")

    assert main([str(local)], env=ENV) == 0

    assert "k8s_launcher" in _by_id(json.loads((local / "resources.json").read_text()))


def test_the_entrypoint_keeps_the_rest_of_an_existing_comm_config(tmp_path):
    local = _kit(tmp_path)
    (local / "comm_config.json").write_text(json.dumps({"backbone_conn_gen": 2}))

    assert main([str(local)], env=ENV) == 0

    assert json.loads((local / "comm_config.json").read_text())["backbone_conn_gen"] == 2


def test_the_entrypoint_fails_closed_on_a_bad_setting(tmp_path, capsys):
    """A half-switched kit would start a process-mode client that runs jobs on the parent's CPU."""
    local = _kit(tmp_path)

    assert main([str(local)], env={k: v for k, v in ENV.items() if k != "FL_K8S_JOB_IMAGE"}) == 1

    assert "FL_K8S_JOB_IMAGE" in capsys.readouterr().err
    assert "process_launcher" in _by_id(json.loads((local / "resources.json").read_text())), "nothing was written"
    assert not (local / "comm_config.json").exists()


def test_the_entrypoint_fails_closed_when_the_kit_has_no_resources(tmp_path, capsys):
    local = tmp_path / "local"
    local.mkdir()

    assert main([str(local)], env=ENV) == 1

    assert "resources.json" in capsys.readouterr().err


def test_the_entrypoint_fails_closed_on_resources_that_are_not_an_object(tmp_path, capsys):
    local = tmp_path / "local"
    local.mkdir()
    (local / "resources.json").write_text("[]")

    assert main([str(local)], env=ENV) == 1

    assert "JSON object" in capsys.readouterr().err
    assert (local / "resources.json").read_text() == "[]"


def test_the_entrypoint_mounts_job_workspaces_where_the_parent_keeps_its_own(tmp_path):
    local = _kit(tmp_path)

    assert main([str(local)], env=ENV) == 0

    launcher = _by_id(json.loads((local / "resources.json").read_text()))["k8s_launcher"]
    assert launcher["args"]["workspace_mount_path"] == str(tmp_path.resolve())


def test_the_entrypoint_refuses_a_workspace_that_holds_the_image_python(tmp_path, monkeypatch, capsys):
    """A job pod mounts an empty workspace at the parent's workspace path: /app would hide the FLIP image's code."""
    local = _kit(tmp_path)
    monkeypatch.setattr(sys, "executable", str(tmp_path / ".venv" / "bin" / "python"))

    assert main([str(local)], env=ENV) == 1

    err = capsys.readouterr().err
    assert str(tmp_path.resolve()) in err
    assert "/var/tmp/nvflare/workspace" in err, "the error says what to do instead"
    assert "process_launcher" in _by_id(json.loads((local / "resources.json").read_text())), "nothing was written"


def test_the_entrypoint_replaces_kit_files_it_cannot_open_for_writing(tmp_path):
    """A kit staged from a Kubernetes Secret arrives read-only (mode 0400) in a directory the client owns."""
    local = _kit(tmp_path)
    (local / "comm_config.json").write_text(json.dumps({"backbone_conn_gen": 2}))
    for name in ("resources.json", "comm_config.json"):
        (local / name).chmod(0o400)

    assert main([str(local)], env=ENV) == 0

    assert "k8s_launcher" in _by_id(json.loads((local / "resources.json").read_text()))
    assert json.loads((local / "comm_config.json").read_text())["backbone_conn_gen"] == 2
    assert not [f for f in local.iterdir() if f.name.startswith(".")], "no temporary files are left behind"


def test_the_entrypoint_fails_closed_when_the_pod_template_is_missing(tmp_path, capsys):
    local = _kit(tmp_path)

    assert main([str(local)], env={**ENV, "FL_K8S_POD_TEMPLATE": str(tmp_path / "absent.yaml")}) == 1

    assert "absent.yaml" in capsys.readouterr().err
    assert not (local / "study_runtime.yaml").exists()


def test_the_module_needs_nothing_beyond_the_standard_library():
    """It runs before NVFLARE starts, like site_policy, and must not fail on a framework import."""
    tree = ast.parse(Path(k8s_job_mode.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported, "no imports found — the guard has drifted"
    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)


def test_runtime_document_puts_the_default_study_beside_each_project():
    # FLIP#1678: the start-up switch and the study-sync sidecar write one format from one function.
    from flip.nvflare.k8s_job_mode import runtime_document

    default = {"container": {"image": "img"}, "pod_template": "pod_specs/job-pod.yaml"}
    project = {"container": {"image": "img"}, "pod_template": {"spec": {}}}

    assert runtime_document(default, {"p1": project}) == {
        "format_version": 2,
        "studies": {"default": default, "p1": project},
    }
