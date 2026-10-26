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

"""The rendered chart's Flower task-pod mode (FLIP#1675): one Kubernetes pod per ClientApp message.

With ``flClient.flower.taskPods.enabled`` the SuperNode runs with ``--isolation process`` and a SuperExec container
starts each ClientApp in a pod of its own, in a namespace of its own. These tests pin what FLIP#1673 found it needs,
that the trust key is never rendered into a ConfigMap, and that a default install renders none of it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
TASK_PODS = "flClient.flower.taskPods"
TASK_NS = "default-flower-tasks"
KEY = "test-trust-internal-key-1675"  # pragma: allowlist secret

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _helm(*extra: str) -> subprocess.CompletedProcess:
    args = [
        "helm", "template", "trust-release", str(CHART_DIR),
        "--set", "flClient.kitHostPath=/opt/flip/fl-kit",
        "--set", "flBackend=flower",
        "--set", "flClient.flower.superlink=superlink:9092",
        "--set", f"secrets.data.trust-internal-service-key={KEY}",
    ]  # fmt: skip
    return subprocess.run([*args, *extra], capture_output=True, text=True, timeout=120)


def _docs(*extra: str) -> list[dict]:
    result = _helm(*extra)
    assert result.returncode == 0, f"helm template failed: {result.stderr}"
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _enabled(*extra: str) -> list[dict]:
    return _docs("--set", f"{TASK_PODS}.enabled=true", *extra)


def _kinds(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d["kind"] == kind]


def _pod_spec(docs: list[dict]) -> dict:
    deployment = next(d for d in _kinds(docs, "Deployment") if d["metadata"]["name"].endswith("-fl-client-net-1"))
    return deployment["spec"]["template"]["spec"]


def _container(docs: list[dict], name: str) -> dict:
    return next(c for c in _pod_spec(docs)["containers"] if c["name"] == name)


def _executor_config(docs: list[dict]) -> dict:
    configmap = next(d for d in _kinds(docs, "ConfigMap") if d["metadata"]["name"].endswith("-flower-executor-net-1"))
    return yaml.safe_load(configmap["data"]["executor-config.yaml"])


def test_a_default_install_renders_no_task_pod_machinery():
    docs = _docs()
    assert [c["name"] for c in _pod_spec(docs)["containers"]] == ["fl-client"]
    assert "--isolation" not in _container(docs, "fl-client")["args"]
    assert not [d for d in docs if d["metadata"].get("namespace") == TASK_NS or d["metadata"]["name"] == TASK_NS]
    assert "automountServiceAccountToken" not in _pod_spec(docs)


def test_task_pods_need_flower():
    result = _helm("--set", "flBackend=nvflare", "--set", f"{TASK_PODS}.enabled=true")
    assert result.returncode != 0
    assert "needs flBackend: flower" in result.stderr


def test_task_pods_refuse_a_gpu_on_the_fl_client():
    result = _helm("--set", f"{TASK_PODS}.enabled=true", "--set", "flClient.gpu.enabled=true")
    assert result.returncode != 0
    assert "flClient.flower.taskPods.resources" in result.stderr


def test_the_supernode_hands_clientapps_to_the_superexec():
    docs = _enabled()
    args = _container(docs, "fl-client")["args"]
    assert args[args.index("--isolation") + 1] == "process"
    command = _container(docs, "superexec")["command"][-1]
    assert "--executor kubernetes" in command
    # Task pods are handed this address, so it is the Service, never loopback.
    assert "--runtime-api-address trust-release-flip-trust-fl-client-net-1-runtime.default.svc:9094" in command


def test_the_trust_key_is_never_rendered_outside_the_secret():
    docs = _enabled()
    for doc in docs:
        if doc["kind"] != "Secret":
            assert KEY not in yaml.safe_dump(doc), f"{doc['kind']} {doc['metadata']['name']} carries the key"
    env = {e["name"]: e for e in _container(docs, "superexec")["env"]}
    assert env["TRUST_INTERNAL_SERVICE_KEY"]["valueFrom"]["secretKeyRef"]["key"] == "trust-internal-service-key"
    # The SuperNode runs no researcher code any more, so it no longer gets the key.
    assert "TRUST_INTERNAL_SERVICE_KEY" not in {e["name"] for e in _container(docs, "fl-client").get("env") or []}


def test_only_the_superexec_gets_an_api_token():
    spec = _pod_spec(_enabled())
    assert spec["automountServiceAccountToken"] is False
    mounted = {
        c["name"] for c in spec["containers"] for m in c.get("volumeMounts", []) if m["name"] == "superexec-token"
    }
    assert mounted == {"superexec"}


def test_task_pods_get_the_trust_apis_by_service_dns_and_no_images_volume():
    config = _executor_config(_enabled())
    assert config["namespace"] == TASK_NS
    env = {e["name"]: e["value"] for e in config["env"]}
    assert env["IMAGING_API_URL"] == "http://imaging-api.default.svc:8000"
    assert env["DATA_ACCESS_API_URL"] == "http://data-access-api.default.svc:8000"
    assert [v["name"] for v in config["volumes"]] == ["dshm"]


def test_the_superexec_role_is_the_minimum_flower_needs():
    docs = _enabled()
    role = next(d for d in _kinds(docs, "Role") if d["metadata"]["name"].endswith("-fl-client-superexec"))
    assert role["metadata"]["namespace"] == TASK_NS
    assert role["rules"] == [
        {"apiGroups": [""], "resources": ["pods", "secrets"], "verbs": ["create", "list", "delete"]}
    ]
    binding = next(d for d in _kinds(docs, "RoleBinding") if d["metadata"]["name"].endswith("-fl-client-superexec"))
    assert binding["subjects"] == [
        {"kind": "ServiceAccount", "name": "trust-release-flip-trust-fl-client", "namespace": "default"}
    ]


def test_task_pods_reach_only_dns_the_runtime_api_and_the_trust_apis():
    docs = _enabled()
    policies = {d["metadata"]["name"]: d for d in _kinds(docs, "NetworkPolicy")}
    task = policies["trust-release-flip-trust-flower-task-pods"]
    assert task["metadata"]["namespace"] == TASK_NS
    assert task["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert "ingress" not in task["spec"]
    reached = {
        (rule["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/component"], rule["ports"][0]["port"])
        for rule in task["spec"]["egress"]
        if "podSelector" in rule["to"][0]
    }
    assert reached == {("fl-client", 9094), ("imaging-api", 8000), ("data-access-api", 8000)}
    for component, port in reached:
        allow = policies[f"trust-release-flip-trust-{component}-from-flower-tasks"]
        assert allow["spec"]["ingress"] == [
            {
                "from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": TASK_NS}}}],
                "ports": [{"port": port, "protocol": "TCP"}],
            }
        ]
