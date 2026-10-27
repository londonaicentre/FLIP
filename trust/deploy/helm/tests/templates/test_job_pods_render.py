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


def _env(container: dict) -> dict:
    return {e["name"]: e for e in container.get("env", [])}


def _job_pod(docs: list[dict]) -> dict:
    return yaml.safe_load(_one(docs, "ConfigMap", "-fl-job-pod-net-1")["data"]["job-pod.yaml"])


def test_a_default_install_renders_no_job_pod_machinery():
    docs = _docs()
    names = {(d["kind"], d["metadata"]["name"]) for d in docs}

    assert not [n for n in names if n[0] in {"Role", "RoleBinding"} and "fl-client" in n[1]]
    assert not [n for n in names if n[1].endswith(("-parent", "-fl-job-pod-net-1"))]
    container = _client_container(docs)
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


def test_the_client_switches_its_kit_before_nvflare_starts():
    docs = _enabled("--set", f"{JOB_PODS}.maxGpus=2", "--set", f"{JOB_PODS}.pendingTimeout=1200")
    container = _client_container(docs)

    script = container["command"][-1]
    switch = script.index(f"python -m flip.nvflare.k8s_job_mode {WORKSPACE}/local")
    assert switch < script.index(f"cd {WORKSPACE}/startup")
    assert script.index(f"cp {WORKSPACE}/local/resources.json.default {WORKSPACE}/local/resources.json") < switch

    env = _env(container)
    assert env["FL_K8S_NAMESPACE"]["valueFrom"] == {"fieldRef": {"fieldPath": "metadata.namespace"}}
    assert env["FL_K8S_PARENT_SERVICE"]["value"] == _one(docs, "Service", "-parent")["metadata"]["name"]
    assert env["FL_K8S_PARENT_PORT"]["value"] == "8102"
    assert env["FL_K8S_JOB_IMAGE"]["value"] == container["image"], "jobs run the image the client runs"
    assert env["FL_K8S_MAX_GPUS"]["value"] == "2"
    assert env["FL_K8S_PENDING_TIMEOUT"]["value"] == "1200"
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
    images = next(m for m in client["volumeMounts"] if m["name"] == "images")
    assert images in job["volumeMounts"], "only this net's slice, at the path imaging-api returns"
    assert {"name": "dshm", "mountPath": "/dev/shm"} in job["volumeMounts"]
    client_spec = _client(docs)["spec"]["template"]["spec"]
    volumes = {v["name"]: v for v in pod["spec"]["volumes"]}
    assert volumes["images"] == next(v for v in client_spec["volumes"] if v["name"] == "images")
    assert volumes["dshm"]["emptyDir"]["medium"] == "Memory"
    # A job pod talks to the FL server directly, by the same name the client does.
    assert pod["spec"]["hostAliases"] == client_spec["hostAliases"]
    assert pod["spec"]["enableServiceLinks"] is False


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


# --- project studies: each job pod mounts only its project's data (FLIP#1678) ---------------------


def _sidecar(docs: list[dict]) -> dict:
    return next(c for c in _client(docs)["spec"]["template"]["spec"]["containers"] if c["name"] == "study-sync")


def _volume(pod_spec: dict, name: str) -> dict:
    return next(v for v in pod_spec["volumes"] if v["name"] == name)


def test_job_pods_add_a_study_sync_sidecar():
    sidecar = _sidecar(_enabled())

    assert sidecar["command"][-2:] == ["flip.nvflare.study_sync", f"{WORKSPACE}/local"]
    assert sidecar["image"] == _client_container(_enabled())["image"]


def test_the_sidecar_writes_local_and_reads_cohort_data_read_only():
    docs = _enabled()
    sidecar = _sidecar(docs)
    mounts = {m["mountPath"]: m for m in sidecar["volumeMounts"]}

    assert mounts[f"{WORKSPACE}/local"]["subPath"] == "local"
    assert mounts["/cohort-data"]["readOnly"] is True
    pod = _client(docs)["spec"]["template"]["spec"]
    claim = _volume(pod, mounts["/cohort-data"]["name"])["persistentVolumeClaim"]
    assert claim["claimName"].endswith("-cohort-data")
    assert claim["readOnly"] is True


def test_the_sidecar_env_names_the_pvcs_and_the_xnat_group():
    env = _env(_sidecar(_enabled()))

    assert env["FL_STUDY_COHORT_DATA_DIR"]["value"] == "/cohort-data"
    assert env["FL_STUDY_COHORT_DATA_PVC"]["value"].endswith("-cohort-data")
    assert env["FL_STUDY_XNAT_ARCHIVE_PVC"]["value"].endswith("-xnat-web")
    assert env["FL_STUDY_XNAT_GID"]["value"] == "1001"
    assert env["FL_K8S_JOB_IMAGE"]["value"] == _env(_client_container(_enabled()))["FL_K8S_JOB_IMAGE"]["value"]
    assert env["TRUST_INTERNAL_SERVICE_KEY"]["valueFrom"]["secretKeyRef"]["key"] == "trust-internal-service-key"


@pytest.mark.parametrize("off", [f"{JOB_PODS}.xnatArchive.enabled=false", "xnat.enabled=false"])
def test_the_xnat_archive_mount_is_off_when_disabled_or_without_an_in_cluster_xnat(off):
    env = _env(_sidecar(_enabled("--set", off)))

    assert env["FL_STUDY_XNAT_ARCHIVE_PVC"]["value"] == ""


def test_data_access_api_stages_into_the_cohort_data_pvc():
    docs = _enabled()
    config = _one(docs, "ConfigMap", "-data-access-api")
    deployment = _one(docs, "Deployment", "-data-access-api")
    pod = deployment["spec"]["template"]["spec"]
    mount = next(m for m in pod["containers"][0]["volumeMounts"] if m["mountPath"] == "/cohort-data")

    assert config["data"]["COHORT_DATA_DIR"] == "/cohort-data"
    assert _volume(pod, mount["name"])["persistentVolumeClaim"]["claimName"].endswith("-cohort-data")
    assert pod["securityContext"]["fsGroup"] == 1000
    pvc = _one(docs, "PersistentVolumeClaim", "-cohort-data")
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]


def test_job_pods_never_mount_a_service_account_token():
    assert _job_pod(_enabled())["spec"]["automountServiceAccountToken"] is False


def test_without_job_pods_nothing_stages_job_data():
    docs = _docs()

    assert [d for d in docs if d["kind"] == "PersistentVolumeClaim" and d["metadata"]["name"].endswith("-cohort-data")] == []
    assert "COHORT_DATA_DIR" not in _one(docs, "ConfigMap", "-data-access-api")["data"]
    assert [c["name"] for c in _client(docs)["spec"]["template"]["spec"]["containers"]] == ["fl-client"]
