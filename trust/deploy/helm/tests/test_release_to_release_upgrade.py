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
"""What a `helm upgrade` from one release of the chart to the next must leave alone (FLIP#1644).

Both failures here were found running the auto-upgrade agent on a kind cluster, and both stop every
release-to-release upgrade of a Kubernetes trust, by hand or by the agent.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")

ENABLE_ALL = (
    "--set",
    "flClient.kitHostPath=/opt/flip/fl-kit",
    "--set",
    "observability.enabled=true",
    "--set",
    "observability.loki.enabled=true",
    "--set",
    "observability.loki.persistence.enabled=true",
)


def _render(chart: Path, *extra: str) -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "flip-trust", str(chart), "--namespace", "flip-trust", *ENABLE_ALL, *extra],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, f"helm template failed:\n{out.stderr}"
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def _claim_templates(docs: list[dict]) -> dict[str, list]:
    return {
        d["metadata"]["name"]: d["spec"]["volumeClaimTemplates"]
        for d in docs
        if d.get("kind") == "StatefulSet" and d["spec"].get("volumeClaimTemplates")
    }


@pytest.fixture(scope="module")
def next_release_chart(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """This chart, stamped as a later release: what the agent pulls for the next upgrade."""
    chart = tmp_path_factory.mktemp("next") / "flip-trust"
    shutil.copytree(CHART_DIR, chart, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    meta = yaml.safe_load((chart / "Chart.yaml").read_text())
    meta["version"], meta["appVersion"] = "99.0.1", "v99.0.1"
    (chart / "Chart.yaml").write_text(yaml.safe_dump(meta))
    return chart


def test_claim_templates_are_identical_across_releases(next_release_chart: Path):
    """volumeClaimTemplates is immutable: a release that changes it cannot be applied at all.

    The labels used to carry helm.sh/chart and app.kubernetes.io/version, which every release bumps,
    so the API server refused every upgrade at the omop-db StatefulSet.
    """
    current, nxt = _claim_templates(_render(CHART_DIR)), _claim_templates(_render(next_release_chart))
    assert current, "no StatefulSet with volumeClaimTemplates rendered; the test lost its subject"
    assert {"flip-trust-omop-db", "flip-trust-xnat-db", "flip-trust-loki"} <= set(current)
    assert current == nxt


def test_claim_template_labels_carry_nothing_release_specific():
    for name, templates in _claim_templates(_render(CHART_DIR)).items():
        for template in templates:
            labels = template["metadata"].get("labels", {})
            assert "helm.sh/chart" not in labels, name
            assert "app.kubernetes.io/version" not in labels, name


def _policies(*extra: str) -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in _render(CHART_DIR, *extra) if d.get("kind") == "NetworkPolicy"}


def _ports(policy: dict) -> set[int]:
    return {p["port"] for rule in policy["spec"].get("egress", []) for p in rule.get("ports", [])}


def test_the_agent_alone_may_reach_the_api_server():
    """The agent's helm calls timed out on 10.96.0.1:443 -> 6443: egress allowed 443 only.

    The API ports go to a policy that selects the agent, never to the namespace-wide egress policy.
    """
    policies = _policies("--set", "autoUpgrade.enabled=true")
    agent = policies["flip-trust-upgrade-agent-apiserver"]
    assert agent["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == "upgrade-agent"
    assert agent["spec"]["policyTypes"] == ["Egress"]
    assert {443, 6443} <= _ports(agent)
    assert 6443 not in _ports(policies["flip-trust-egress"])


def test_no_api_server_path_without_the_agent():
    assert "flip-trust-upgrade-agent-apiserver" not in _policies()


def test_api_server_ports_are_configurable():
    policies = _policies("--set", "autoUpgrade.enabled=true", "--set", "autoUpgrade.apiServerPorts={8443}")
    assert _ports(policies["flip-trust-upgrade-agent-apiserver"]) == {8443}
