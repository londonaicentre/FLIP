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

"""The rendered chart's NVFLARE job-pod mode (FLIP#1428): one Kubernetes pod per FL job.

With ``flClient.nvflare.jobPods.enabled`` the fl-client becomes a small parent that keeps the site
registered, and NVFLARE's ``ClientK8sJobLauncher`` starts each job in its own pod, asking Kubernetes
for the job's GPUs. These tests render the chart and pin what that needs: RBAC for the launcher, a
Service job pods reach the parent through, the settings ``flip.nvflare.k8s_job_mode`` reads, and a
job-pod template that gives each job what the client container has (its env, the trust-internal
key, this net's images slice, /dev/shm and the FL server's host aliases).

They also pin the other half: off by default, and a default install renders none of it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
KIT_HOST_PATH = "/opt/flip/fl-kit"
JOB_PODS = "flClient.nvflare.jobPods"
# NVFLARE mounts a job pod's workspace at the parent's workspace path, so the parent cannot use /app (the image).
WORKSPACE = "/var/tmp/nvflare/workspace"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _helm(*extra: str) -> subprocess.CompletedProcess:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", f"flClient.kitHostPath={KIT_HOST_PATH}"]
    return subprocess.run([*args, *extra], capture_output=True, text=True, timeout=120)


def _docs(*extra: str) -> list[dict]:
    result = _helm(*extra)
    assert result.returncode == 0, f"helm template failed: {result.stderr}"
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _enabled(*extra: str) -> list[dict]:
    return _docs("--set", f"{JOB_PODS}.enabled=true", *extra)


def _one(docs: list[dict], kind: str, name_suffix: str) -> dict:
    matches = [d for d in docs if d["kind"] == kind and d["metadata"]["name"].endswith(name_suffix)]
    names = [m["metadata"]["name"] for m in matches]
    assert len(matches) == 1, f"expected one {kind} named *{name_suffix}, found {names}"
    return matches[0]


def _client(docs: list[dict]) -> dict:
    return _one(docs, "Deployment", "-fl-client-net-1")


def _client_container(docs: list[dict]) -> dict:
    return next(c for c in _client(docs)["spec"]["template"]["spec"]["containers"] if c["name"] == "fl-client")


def _init_container(docs: list[dict]) -> dict:
    return next(c for c in _client(docs)["spec"]["template"]["spec"]["initContainers"] if c["name"] == "fl-client-init")


def _env(container: dict) -> dict:
    return {e["name"]: e for e in container.get("env", [])}


def _job_pod(docs: list[dict]) -> dict:
    return yaml.safe_load(_one(docs, "ConfigMap", "-fl-job-pod-net-1")["data"]["job-pod.yaml"])


def test_a_default_install_renders_no_job_pod_machinery():
    docs = _docs()
    names = {(d["kind"], d["metadata"]["name"]) for d in docs}

    assert not [n for n in names if n[0] in {"Role", "RoleBinding"} and "fl-client" in n[1]]
    assert not [n for n in names if n[1].endswith(("-parent", "-fl-job-pod-net-1"))]
    for container in (_client_container(docs), _init_container(docs)):
        assert "k8s_job_mode" not in "\n".join(container["command"])
        assert not [n for n in _env(container) if n.startswith("FL_K8S_")]


def test_the_launcher_may_manage_job_pods_and_their_secrets_in_its_namespace_only():
    docs = _enabled()

    role = _one(docs, "Role", "-fl-client-launcher")
    rules = {tuple(r["resources"]): sorted(r["verbs"]) for r in role["rules"]}
    assert rules == {
        ("pods",): sorted(["create", "delete", "get", "list", "watch"]),
        ("events",): sorted(["get", "list", "watch"]),
        # The startup-kit and per-job credential Secrets the launcher mounts into each job pod.
        ("secrets",): sorted(["create", "get", "update", "patch", "delete"]),
    }
    binding = _one(docs, "RoleBinding", "-fl-client-launcher")
    assert binding["roleRef"]["name"] == role["metadata"]["name"]
    sa = _client(docs)["spec"]["template"]["spec"]["serviceAccountName"]
    assert binding["subjects"] == [{"kind": "ServiceAccount", "name": sa, "namespace": role["metadata"]["namespace"]}]


def test_job_pods_reach_the_parent_through_a_service_that_selects_only_the_parent():
    docs = _enabled()

    service = _one(docs, "Service", "-fl-client-net-1-parent")
    assert [p["port"] for p in service["spec"]["ports"]] == [8102]
    selector = service["spec"]["selector"]
    pod_labels = _client(docs)["spec"]["template"]["metadata"]["labels"]
    assert selector.items() <= pod_labels.items(), "the Service must select the parent pod"
    job_labels = _job_pod(docs)["metadata"]["labels"]
    assert not selector.items() <= job_labels.items(), "a job pod must never receive the parent's traffic"
    ports = [p["containerPort"] for p in _client_container(docs).get("ports", [])]
    assert 8102 in ports


def test_the_init_container_switches_the_kit_before_nvflare_starts():
    """fl-client-init does the switch, after rendering resources.json; the client, whose kit is read-only
    (FLIP#1570), refuses to start without the switched kit's study_runtime.yaml."""
    docs = _enabled(
        "--set", f"{JOB_PODS}.maxGpus=2", "--set", f"{JOB_PODS}.pendingTimeout=1200",
        "--set", f"{JOB_PODS}.ephemeralStorage=40Gi",
    )  # fmt: skip
    container = _init_container(docs)
    client = _client_container(docs)

    script = container["command"][-1]
    switch = script.index(f"python -m flip.nvflare.k8s_job_mode {WORKSPACE}/local")
    assert script.index(f"/app/fl-client-init.sh {WORKSPACE}") < switch
    assert "k8s_job_mode" not in client["command"][-1]
    assert "study_runtime.yaml" in client["command"][-1]
    assert not [n for n in _env(client) if n.startswith("FL_K8S_")], "only the init container reads them"

    env = _env(container)
    assert env["FL_K8S_NAMESPACE"]["valueFrom"] == {"fieldRef": {"fieldPath": "metadata.namespace"}}
    assert env["FL_K8S_PARENT_SERVICE"]["value"] == _one(docs, "Service", "-parent")["metadata"]["name"]
    assert env["FL_K8S_PARENT_PORT"]["value"] == "8102"
    assert env["FL_K8S_JOB_IMAGE"]["value"] == client["image"], "jobs run the image the client runs"
    assert env["FL_K8S_MAX_GPUS"]["value"] == "2"
    assert env["FL_K8S_PENDING_TIMEOUT"]["value"] == "1200"
    assert env["FL_K8S_EPHEMERAL_STORAGE"]["value"] == "40Gi"
    assert env["FL_K8S_POD_TEMPLATE"]["value"] == "/app/k8s/job-pod.yaml"
    mount = next(m for m in container["volumeMounts"] if m["mountPath"] == "/app/k8s")
    volume = next(v for v in _client(docs)["spec"]["template"]["spec"]["volumes"] if v["name"] == mount["name"])
    assert volume["configMap"]["name"].endswith("-fl-job-pod-net-1")


def test_a_job_pod_gets_what_the_client_container_has():
    docs = _enabled()
    client = _client_container(docs)
    pod = _job_pod(docs)
    [job] = pod["spec"]["containers"]

    assert pod["kind"] == "Pod"
    assert pod["metadata"]["labels"]["app.kubernetes.io/component"] == "fl-job"
    assert pod["metadata"]["labels"]["flip/network"] == "net-1"
    # The launcher sets image, command and resources on the container NVFLARE calls nvflare_job.
    assert job["name"] == "nvflare_job"
    assert job["imagePullPolicy"] == client["imagePullPolicy"]
    assert job["envFrom"] == client["envFrom"]
    env = _env(job)
    for name in ("TRUST_INTERNAL_SERVICE_KEY_HEADER", "TRUST_INTERNAL_SERVICE_KEY"):
        assert env[name] == _env(client)[name]
    client_spec = _client(docs)["spec"]["template"]["spec"]
    volumes = {v["name"]: v for v in pod["spec"]["volumes"]}
    assert volumes["dshm"]["emptyDir"]["medium"] == "Memory"
    assert pod["spec"]["automountServiceAccountToken"] is False
    # None by default: both backends dial the hub's public FL name.
    assert "hostAliases" not in pod["spec"]
    assert "hostAliases" not in client_spec
    assert pod["spec"]["enableServiceLinks"] is False


def test_the_study_file_is_the_operators_configmap_which_the_client_only_reads():
    """NVFLARE's study file is the site's to maintain (FLIP#1678): the chart mounts it and never creates it."""
    docs = _enabled()
    spec = _client(docs)["spec"]["template"]["spec"]
    name = "trust-release-flip-trust-fl-studies-net-1"

    volume = next(v for v in spec["volumes"] if v["name"] == "fl-studies")
    assert volume["configMap"] == {"name": name, "optional": True}
    created = [d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == name]
    assert not created, "a helm upgrade would reset it"
    # make sync-project-studies writes the ConfigMap under the same name.
    makefile = (Path(__file__).parents[2] / "Makefile").read_text()
    assert "$(RELEASE_NAME)-flip-trust-fl-studies-$(FL_NET)" in makefile
    mount = next(m for m in _client_container(docs)["volumeMounts"] if m["name"] == "fl-studies")
    assert mount == {"name": "fl-studies", "mountPath": "/app/studies", "readOnly": True}
    # fl-client-init links local/study_runtime.yaml to it; it does not mount it, so it cannot write it.
    init = _init_container(docs)
    assert _env(init)["FL_K8S_STUDIES_FILE"]["value"] == "/app/studies/study_runtime.yaml"
    assert "fl-studies" not in [m["name"] for m in init["volumeMounts"]]


def test_the_template_never_mounts_images_and_streams():
    """Only a project's own NVFLARE study adds an images mount, so a job outside one cannot see the cache."""
    for extra in ((), ("--set", f"{JOB_PODS}.images=stream")):
        [job] = _job_pod(_enabled(*extra))["spec"]["containers"]

        assert _env(job)["IMAGES_TRANSPORT"]["value"] == "stream"
        # Its IMAGES_DIR is a scratch folder of its own, for the files add_resource uploads (FLIP#1049).
        assert job["volumeMounts"] == [
            {"name": "dshm", "mountPath": "/dev/shm"},
            {"name": "images-scratch", "mountPath": "/app/data/images"},
        ]


def test_by_default_a_job_pod_declares_the_shared_images_volume_for_its_study_to_mount():
    """The fl-client mounts the project's folder of it in the job's NVFLARE study (FLIP#1678)."""
    docs = _enabled()
    pod = _job_pod(docs)
    client_spec = _client(docs)["spec"]["template"]["spec"]

    volumes = {v["name"]: v for v in pod["spec"]["volumes"]}
    assert volumes["images"] == next(v for v in client_spec["volumes"] if v["name"] == "images")
    # The job pod attaches the fl-client's volume, so it is placed by the fl-client's rule.
    assert pod["spec"]["affinity"] == client_spec["affinity"]


def test_a_streaming_job_pod_mounts_no_images_volume_and_runs_anywhere():
    """It streams its studies into its own run directory (FLIP#1049)."""
    pod = _job_pod(_enabled("--set", f"{JOB_PODS}.images=stream"))

    volumes = {v["name"]: v for v in pod["spec"]["volumes"]}
    assert list(volumes) == ["dshm", "images-scratch"]
    assert volumes["images-scratch"] == {"name": "images-scratch", "emptyDir": {}}
    assert "affinity" not in pod["spec"]


def test_a_project_mount_is_refused_without_the_shared_images_volume():
    result = _helm("--set", f"{JOB_PODS}.enabled=true", "--set", "sharedImagesPvc.enabled=false")

    assert result.returncode != 0
    assert "images: stream" in result.stderr


def test_an_unknown_way_to_get_images_is_refused():
    result = _helm("--set", f"{JOB_PODS}.enabled=true", "--set", f"{JOB_PODS}.images=mount")

    assert result.returncode != 0
    assert "project (a mount" in result.stderr


def test_a_job_pod_resolves_the_fl_server_as_the_client_does():
    alias = [{"ip": "203.0.113.10", "hostnames": ["fl.example.org"]}]
    docs = _enabled(
        "--set",
        "flClient.hostAliases[0].ip=203.0.113.10",
        "--set",
        "flClient.hostAliases[0].hostnames[0]=fl.example.org",
    )
    assert _client(docs)["spec"]["template"]["spec"]["hostAliases"] == alias
    assert _job_pod(docs)["spec"]["hostAliases"] == alias


def test_job_pods_take_the_gpu_pool_placement_settings():
    docs = _enabled(
        "--set", f"{JOB_PODS}.tolerations[0].key=sku",
        "--set", f"{JOB_PODS}.tolerations[0].value=gpu",
        "--set", f"{JOB_PODS}.tolerations[0].effect=NoSchedule",
        "--set", f"{JOB_PODS}.nodeSelector.pool=gpu",
    )  # fmt: skip
    spec = _job_pod(docs)["spec"]

    assert spec["tolerations"] == [{"key": "sku", "value": "gpu", "effect": "NoSchedule"}]
    assert spec["nodeSelector"] == {"pool": "gpu"}


def test_the_bucket_reaches_job_pods_when_set():
    docs = _enabled("--set", "uploadedFederatedDataBucket=s3://example-bucket")

    assert _env(_job_pod(docs)["spec"]["containers"][0])["UPLOADED_FEDERATED_DATA_BUCKET"]["value"] == (
        "s3://example-bucket"
    )


def test_job_pods_are_refused_on_flower():
    result = _helm("--set", f"{JOB_PODS}.enabled=true", "--set", "flBackend=flower")

    assert result.returncode != 0
    assert "NVFLARE" in result.stderr


def test_a_parent_that_holds_a_gpu_is_refused():
    """The GPUs go to the job pods; a GPU on the parent would sit idle between jobs, the cost this mode removes."""
    result = _helm("--set", f"{JOB_PODS}.enabled=true", "--set", "flClient.gpu.enabled=true")

    assert result.returncode != 0
    assert "jobPods.maxGpus" in result.stderr


def test_only_the_parent_may_reach_an_api_server_off_port_443():
    """The launcher calls the Kubernetes API from the parent. NetworkPolicy sees the API server's real endpoint
    (minikube 8443, kind 6443), not the kubernetes Service's 443, so a cluster that listens elsewhere names it."""
    docs = _enabled(
        "--set", f"{JOB_PODS}.apiServer.cidrs[0]=192.168.58.2/32",
        "--set", f"{JOB_PODS}.apiServer.port=8443",
    )  # fmt: skip

    policy = _one(docs, "NetworkPolicy", "-fl-client-api-server")
    assert policy["spec"]["policyTypes"] == ["Egress"]
    assert policy["spec"]["egress"] == [
        {"to": [{"ipBlock": {"cidr": "192.168.58.2/32"}}], "ports": [{"port": 8443, "protocol": "TCP"}]}
    ]
    selector = policy["spec"]["podSelector"]["matchLabels"]
    assert selector.items() <= _client(docs)["spec"]["template"]["metadata"]["labels"].items()
    assert not selector.items() <= _job_pod(docs)["metadata"]["labels"].items(), "job pods never get API access"


def test_no_api_server_rule_without_cidrs_or_without_network_policies():
    assert not [d for d in _enabled() if d["metadata"]["name"].endswith("-fl-client-api-server")]
    docs = _enabled("--set", f"{JOB_PODS}.apiServer.cidrs[0]=10.0.0.1/32", "--set", "networkPolicies.enabled=false")
    assert not [d for d in docs if d["metadata"]["name"].endswith("-fl-client-api-server")]


@pytest.mark.parametrize("backend", ["nvflare", "flower"])
def test_a_rollout_never_runs_two_clients_with_one_identity(backend):
    """A trust's FL client is one identity (one NVFLARE site, one SuperNode credential). Under RollingUpdate the new
    pod starts before the old one stops, the two evict each other's connection to the server, and the server drops
    the site — on the job-pod parent that aborts whatever job started meanwhile."""
    docs = _docs("--set", f"flBackend={backend}")

    # rollingUpdate is cleared explicitly: an existing Deployment holds the API server's RollingUpdate defaults, and
    # Helm's server-side apply refuses Recreate beside them ("may not be specified when strategy type is Recreate").
    assert _client(docs)["spec"]["strategy"] == {"type": "Recreate", "rollingUpdate": None}


def test_the_parent_keeps_its_workspace_off_the_image_directory():
    docs = _enabled()
    container = _client_container(docs)
    mounts = {m["mountPath"]: m for m in container["volumeMounts"]}

    for sub in ("local", "startup", "transfer"):
        assert mounts[f"{WORKSPACE}/{sub}"]["subPath"] == sub
        assert f"/app/{sub}" not in mounts
    # The switch resolves the workspace from local/'s parent, so the init container mounts the kit there too.
    init_mounts = {m["mountPath"]: m for m in _init_container(docs)["volumeMounts"]}
    for sub in ("local", "startup"):
        assert mounts[f"{WORKSPACE}/{sub}"].get("readOnly") is True
        assert init_mounts[f"{WORKSPACE}/{sub}"]["subPath"] == sub
    volumes = {v["name"]: v for v in _client(docs)["spec"]["template"]["spec"]["volumes"]}
    workspace = volumes[mounts[WORKSPACE]["name"]]
    assert "emptyDir" in workspace, "the workspace root must be writable for per-job directories"
    lines = container["command"][-1].splitlines()
    commands = "\n".join(line for line in lines if not line.strip().startswith("#"))
    assert "/app/local" not in commands
    assert "/app/startup" not in commands


def test_process_mode_keeps_the_app_workspace():
    container = _client_container(_docs())
    mount_paths = {m["mountPath"] for m in container["volumeMounts"]}

    assert {"/app/local", "/app/startup", "/app/transfer"} <= mount_paths
    assert WORKSPACE not in mount_paths
    assert "cd /app/startup" in container["command"][-1]


# --- job-pod leftovers (FLIP#1428): a CPU/memory reservation, and finished pods deleted -------------------------


def test_a_job_pod_reserves_cpu_and_memory_through_a_no_op_init_container():
    """NVFLARE replaces the job container's resources, so the reservation sits on an init container, which also keeps
    the pod out of BestEffort, the class the XNAT conversion quota counts."""
    docs = _enabled("--set", f"{JOB_PODS}.requests.cpu=2", "--set", f"{JOB_PODS}.requests.memory=8Gi")
    [reserve] = _job_pod(docs)["spec"]["initContainers"]

    assert reserve["command"] == ["/bin/true"]
    assert reserve["resources"] == {"requests": {"cpu": 2, "memory": "8Gi"}}
    assert reserve["image"] == _env(_init_container(docs))["FL_K8S_JOB_IMAGE"]["value"]
    assert reserve["securityContext"] == _client_container(docs)["securityContext"]


def test_a_pruner_beside_the_parent_deletes_this_nets_finished_job_pods():
    docs = _enabled("--set", f"{JOB_PODS}.keepFinishedHours=6")
    containers = {c["name"]: c for c in _client(docs)["spec"]["template"]["spec"]["containers"]}
    pruner = containers["job-pod-pruner"]
    env = _env(pruner)

    assert pruner["command"] == ["/app/.venv/bin/python", "-m", "flip.nvflare.prune_job_pods"]
    assert pruner["image"] == containers["fl-client"]["image"]
    assert pruner["resources"]["requests"], "a BestEffort container would count against the XNAT conversion quota"
    assert env["FL_JOB_POD_KEEP_HOURS"]["value"] == "6"
    assert env["FL_K8S_NAMESPACE"]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.namespace"
    # The selector picks exactly this release's job pods on this net.
    wanted = dict(pair.split("=") for pair in env["FL_JOB_POD_SELECTOR"]["value"].split(","))
    job_labels = _job_pod(docs)["metadata"]["labels"]
    assert wanted.items() <= job_labels.items()
    assert wanted["app.kubernetes.io/component"] == "fl-job"
    assert wanted["flip/network"] == "net-1"


def test_process_mode_runs_no_pruner():
    names = [c["name"] for c in _client(_docs())["spec"]["template"]["spec"]["containers"]]

    assert names == ["fl-client"]
