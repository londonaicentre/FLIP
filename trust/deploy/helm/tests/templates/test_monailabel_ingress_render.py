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

"""The MONAI Label NetworkPolicy never opens the port to every source by default.

The API is unauthenticated and holds the XNAT service-account credentials, and an ingress rule with no
``from:`` matches every source. So while the policy renders, an empty ``allowedIngressCIDRs`` fails the
render; opening it to everyone is ``["0.0.0.0/0"]``, written down.
Needs helm, so the chart workflow's helm-template job runs it; the pytest job skips it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")

ENABLED = ["monailabel.enabled=true", "monailabel.publicUrl=http://node.example.com:30030"]


def _render(*sets: str) -> subprocess.CompletedProcess:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    for item in sets:
        args += ["--set", item]
    args += ["--show-only", "templates/monailabel.yaml"]
    return subprocess.run(args, capture_output=True, text=True)


def _policy(rendered: str) -> dict | None:
    docs = [doc for doc in yaml.safe_load_all(rendered) if doc]
    policies = [doc for doc in docs if doc["kind"] == "NetworkPolicy"]
    assert len(policies) <= 1
    return policies[0] if policies else None


def test_an_empty_cidr_list_fails_the_render():
    result = _render(*ENABLED)
    assert result.returncode != 0
    assert "monailabel.allowedIngressCIDRs is empty" in result.stderr


def test_the_rule_is_scoped_to_the_listed_cidrs():
    result = _render(*ENABLED, "monailabel.allowedIngressCIDRs[0]=10.0.0.0/8")
    assert result.returncode == 0, result.stderr
    policy = _policy(result.stdout)
    assert policy is not None
    (rule,) = policy["spec"]["ingress"]
    assert rule["from"] == [{"ipBlock": {"cidr": "10.0.0.0/8"}}]


def test_an_open_rule_must_be_written_down():
    result = _render(*ENABLED, "monailabel.allowedIngressCIDRs[0]=0.0.0.0/0")
    assert result.returncode == 0, result.stderr
    (rule,) = _policy(result.stdout)["spec"]["ingress"]
    assert rule["from"] == [{"ipBlock": {"cidr": "0.0.0.0/0"}}]


@pytest.mark.parametrize("switch", ["networkPolicies.enabled=false", "monailabel.service.allowExternalIngress=false"])
def test_no_policy_needs_no_cidrs(switch):
    result = _render(*ENABLED, switch)
    assert result.returncode == 0, result.stderr
    assert _policy(result.stdout) is None
