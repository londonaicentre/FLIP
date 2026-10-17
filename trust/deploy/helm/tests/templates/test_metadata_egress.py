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

"""The cloud instance-metadata egress rule (169.254.169.254), rendered (FLIP#1390).

Its one consumer is the omop-db vocab-load Job reaching S3 through an AWS node role. Every other
deployment (kind, AKS) must not open the metadata endpoint, a known SSRF/credential-theft target,
to every pod. Asserted on `helm template` output. Skipped without helm.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
IMDS = "169.254.169.254/32"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _render(*sets: str) -> str:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    for item in sets:
        args += ["--set", item]
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=120).stdout


def _egress_rules(rendered: str) -> list[dict]:
    for doc in yaml.safe_load_all(rendered):
        if doc and doc.get("kind") == "NetworkPolicy" and doc["metadata"]["name"].endswith("-egress"):
            return doc["spec"]["egress"]
    raise AssertionError("no egress NetworkPolicy rendered")


def _reaches_imds_on_port_80(rules: list[dict]) -> bool:
    """Whether any rule lets a pod open TCP 80 (where IMDS listens) to 169.254.169.254."""
    for rule in rules:
        ports = rule.get("ports")
        if ports is not None and not any(p.get("port") == 80 for p in ports):
            continue
        peers = rule.get("to")
        if peers is None:
            return True  # no destination limit at all
        for peer in peers:
            block = peer.get("ipBlock")
            if block and block["cidr"] in ("0.0.0.0/0", IMDS) and IMDS not in block.get("except", []):
                return True
    return False


def test_no_metadata_egress_by_default():
    """Not merely the absence of an explicit rule: the any-destination HTTP rule must exclude it too."""
    rules = _egress_rules(_render())
    assert not _reaches_imds_on_port_80(rules), rules
    # ...while DNS/HTTP/HTTPS still reach the internet and every pod in the cluster.
    first = rules[0]
    assert {"namespaceSelector": {}} in first["to"], "in-cluster peers (kube-dns) must stay reachable"
    assert {"ipBlock": {"cidr": "0.0.0.0/0", "except": [IMDS]}} in first["to"]


def test_the_vocab_load_job_on_a_node_role_gets_metadata_egress():
    rules = _egress_rules(_render("omopDb.vocabLoad.enabled=true", "omopDb.vocabLoad.s3Bucket=some-bucket"))
    assert _reaches_imds_on_port_80(rules)


def test_a_vocab_load_job_with_mounted_credentials_needs_no_metadata():
    rendered = _render(
        "omopDb.vocabLoad.enabled=true",
        "omopDb.vocabLoad.s3Bucket=some-bucket",
        "omopDb.vocabLoad.hostAwsMount.enabled=true",
        "omopDb.vocabLoad.hostAwsMount.hostPath=/home/op/.aws",
    )
    assert not _reaches_imds_on_port_80(_egress_rules(rendered))
