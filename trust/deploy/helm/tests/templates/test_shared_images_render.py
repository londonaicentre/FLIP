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

"""imaging-api and fl-client are held to one node only while they share an RWO volume (FLIP#1439).

A ReadWriteOnce sharedImagesPvc mounts from one node, so both pods carry flip/shared-images, require
podAffinity to it and restart Recreate. On ReadWriteMany, or with the volume off, nothing ties them
together and the chart drops all three. Skipped without helm; the chart workflow's helm-template job
runs it.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
COMPONENTS = ("imaging-api", "fl-client")
TERM = {"labelSelector": {"matchLabels": {"flip/shared-images": "true"}}, "topologyKey": "kubernetes.io/hostname"}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _deployments(*extra: str) -> dict[str, dict]:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    result = subprocess.run([*args, *extra], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"helm template failed: {result.stderr}"
    return {
        doc["metadata"]["labels"]["app.kubernetes.io/component"]: doc
        for doc in yaml.safe_load_all(result.stdout)
        if doc
        and doc.get("kind") == "Deployment"
        and doc["metadata"]["labels"].get("app.kubernetes.io/component") in COMPONENTS
    }


def _required(deployment: dict) -> list:
    affinity = deployment["spec"]["template"]["spec"].get("affinity", {})
    return affinity.get("podAffinity", {}).get("requiredDuringSchedulingIgnoredDuringExecution", [])


def test_rwo_holds_both_pods_on_one_node():
    deployments = _deployments()
    assert set(deployments) == set(COMPONENTS)
    for name, deployment in deployments.items():
        assert deployment["spec"]["template"]["metadata"]["labels"].get("flip/shared-images") == "true", name
        assert TERM in _required(deployment), name
        assert deployment["spec"]["strategy"] == {"type": "Recreate"}, name


@pytest.mark.parametrize(
    "setting", ["sharedImagesPvc.accessMode=ReadWriteMany", "sharedImagesPvc.enabled=false"], ids=["rwx", "off"]
)
def test_without_an_rwo_volume_nothing_ties_them(setting):
    deployments = _deployments("--set", setting)
    assert set(deployments) == set(COMPONENTS)
    for name, deployment in deployments.items():
        assert "flip/shared-images" not in deployment["spec"]["template"]["metadata"]["labels"], name
        assert _required(deployment) == [], name
        assert "strategy" not in deployment["spec"], name
    # The operator's part of imaging-api's affinity survives.
    assert "podAntiAffinity" in deployments["imaging-api"]["spec"]["template"]["spec"]["affinity"]


def test_an_operators_own_required_term_is_kept_beside_the_charts():
    own = {"labelSelector": {"matchLabels": {"site/gpu": "true"}}, "topologyKey": "kubernetes.io/hostname"}
    affinity = {"podAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [own]}}
    terms = _required(_deployments("--set-json", f"flClient.affinity={json.dumps(affinity)}")["fl-client"])
    assert terms == [own, TERM]
