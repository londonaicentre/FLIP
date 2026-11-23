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

"""``networkPolicies.apiServer`` lets the pods that call the Kubernetes API reach it off port 443 (FLIP#1958).

NetworkPolicy matches the endpoint the ``kubernetes`` Service forwards to, not the Service's 443, and k3s (which
enforces NetworkPolicy out of the box), kind and minikube listen on 6443 or 8443. Two parts of a trust call the API:
XNAT's Container Service, from xnat-web, which starts dcm2niix as Jobs; and NVFLARE's job launcher on the job-pod
parent. Only the second had a setting (``flClient.nvflare.jobPods.apiServer``), so on k3s ``xnat-init`` failed with
"Container Service cannot reach its kubernetes backend". One cluster-level setting now covers both, and the old one
keeps working for the parent. Skipped without helm; the chart workflow's helm-template job runs it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
JOB_PODS = "flClient.nvflare.jobPods"
K3S = ("--set", "networkPolicies.apiServer.cidrs[0]=10.246.169.48/32", "--set", "networkPolicies.apiServer.port=6443")
RULE = [{"to": [{"ipBlock": {"cidr": "10.246.169.48/32"}}], "ports": [{"port": 6443, "protocol": "TCP"}]}]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _docs(*extra: str) -> list[dict]:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    result = subprocess.run([*args, *extra], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"helm template failed: {result.stderr}"
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _named(docs: list[dict], kind: str, suffix: str) -> list[dict]:
    return [d for d in docs if d["kind"] == kind and d["metadata"]["name"].endswith(suffix)]


def _pod_labels(docs: list[dict], deployment_suffix: str) -> dict:
    (deploy,) = _named(docs, "Deployment", deployment_suffix)
    return deploy["spec"]["template"]["metadata"]["labels"]


def test_xnat_web_may_reach_the_api_server_it_is_given():
    docs = _docs(*K3S)

    (policy,) = _named(docs, "NetworkPolicy", "-xnat-web-api-server")
    assert policy["spec"]["policyTypes"] == ["Egress"]
    assert policy["spec"]["egress"] == RULE
    selector = policy["spec"]["podSelector"]["matchLabels"]
    assert selector.items() <= _pod_labels(docs, "-xnat-web").items()
    for other in ("-trust-api", "-imaging-api", "-data-access-api", "-orthanc"):
        assert not selector.items() <= _pod_labels(docs, other).items(), f"only xnat-web gets it, not {other}"


@pytest.mark.parametrize(
    "extra",
    [(), (*K3S, "--set", "networkPolicies.enabled=false"), (*K3S, "--set", "xnat.web.enabled=false")],
    ids=["no-endpoint-named", "network-policies-off", "no-xnat-web"],
)
def test_no_xnat_web_api_rule_when_nothing_needs_it(extra):
    assert not _named(_docs(*extra), "NetworkPolicy", "-xnat-web-api-server")


def test_the_job_pod_parent_follows_the_cluster_setting():
    docs = _docs(*K3S, "--set", f"{JOB_PODS}.enabled=true")

    (policy,) = _named(docs, "NetworkPolicy", "-fl-client-api-server")
    assert policy["spec"]["egress"] == RULE


def test_the_job_pods_own_setting_still_wins_for_the_parent():
    docs = _docs(
        *K3S,
        "--set", f"{JOB_PODS}.enabled=true",
        "--set", f"{JOB_PODS}.apiServer.cidrs[0]=192.168.58.2/32",
        "--set", f"{JOB_PODS}.apiServer.port=8443",
    )  # fmt: skip

    (parent,) = _named(docs, "NetworkPolicy", "-fl-client-api-server")
    assert parent["spec"]["egress"] == [
        {"to": [{"ipBlock": {"cidr": "192.168.58.2/32"}}], "ports": [{"port": 8443, "protocol": "TCP"}]}
    ]
    (xnat,) = _named(docs, "NetworkPolicy", "-xnat-web-api-server")
    assert xnat["spec"]["egress"] == RULE, "xnat-web reads only the cluster setting"


@pytest.mark.parametrize(
    "bad",
    [
        ("--set", "networkPolicies.apiServer.cidr[0]=10.0.0.1/32"),
        ("--set", "networkPolicies.apiServer.cidrs=10.0.0.1/32"),
        ("--set", "networkPolicies.apiServer.port=0"),
        ("--set", "networkPolicies.apiServer.port=70000"),
    ],
    ids=["misspelt-key", "scalar-cidrs", "port-zero", "port-too-high"],
)
def test_a_mistyped_api_server_setting_fails_the_render_instead_of_rendering_no_policy(bad):
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    result = subprocess.run([*args, *bad], capture_output=True, text=True, timeout=120)

    assert result.returncode != 0
    assert "apiServer" in result.stderr
