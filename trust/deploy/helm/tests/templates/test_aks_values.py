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

"""values-aks.yaml, the overlay for a managed AKS cluster (FLIP#1390), rendered with the chart.

Asserted on `helm template` output: the kit comes from a Secret, Postgres keeps its data below the
fresh disk's lost+found, and nothing reaches the cloud metadata endpoint. Skipped without helm.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _docs() -> list[dict]:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "-f", str(CHART_DIR / "values-aks.yaml")]
    rendered = subprocess.run(args, capture_output=True, text=True, check=True, timeout=120).stdout
    return [d for d in yaml.safe_load_all(rendered) if d]


def test_aks_overlay_renders_without_a_node_path():
    docs = _docs()
    fl = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("fl-client-net-1"))
    volumes = {v["name"]: v for v in fl["spec"]["template"]["spec"]["volumes"]}
    assert "emptyDir" in volumes["fl-client-kit"]
    assert not any("hostPath" in v for n, v in volumes.items() if n.startswith("fl-client-kit"))


def test_aks_overlay_puts_postgres_data_below_lost_and_found():
    for doc in _docs():
        if doc["kind"] == "StatefulSet" and doc["metadata"]["name"].endswith(("omop-db", "xnat-db")):
            env = {e["name"]: e.get("value") for e in doc["spec"]["template"]["spec"]["containers"][0]["env"]}
            assert env.get("PGDATA") == "/var/lib/postgresql/data/pgdata", doc["metadata"]["name"]


def test_aks_overlay_never_opens_the_metadata_endpoint():
    egress = next(d for d in _docs() if d["kind"] == "NetworkPolicy" and d["metadata"]["name"].endswith("-egress"))
    peers = [p for rule in egress["spec"]["egress"] for p in rule.get("to", [])]
    assert not any(p.get("ipBlock", {}).get("cidr") == "169.254.169.254/32" for p in peers)


def test_make_deploy_layers_the_platform_values_last():
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    out = subprocess.run(
        [
            "make",
            "-n",
            "-C",
            str(CHART_DIR),
            "deploy",
            "PLATFORM_VALUES=values-aks.yaml",
            "OVERRIDES_FILE=k8s-trust-X.yaml",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    assert "-f values-aks.yaml" in out
    assert out.index("-f k8s-trust-X.yaml") < out.index("-f values-aks.yaml"), out


def _cpu_m(q: str) -> int:
    return int(q[:-1]) if q.endswith("m") else int(float(q) * 1000)


def _mem_mi(q: str) -> int:
    return int(q[:-2]) * 1024 if q.endswith("Gi") else int(q[:-2])


def test_small_node_overlay_fits_the_trust_on_one_four_vcpu_node():
    """A Standard_D4s_v5 offers about 3.86 CPUs and 12.6 GiB to pods, and AKS's own system pods
    request about 0.6 CPU and 1 GiB of that (measured on the trial cluster). The rest must hold
    every trust pod's requests, the busiest init container included."""
    args = ["helm", "template", "trust-release", str(CHART_DIR)]
    for f in ("values-aks.yaml", "values-small-node.yaml"):
        args += ["-f", str(CHART_DIR / f)]
    rendered = subprocess.run(args, capture_output=True, text=True, check=True, timeout=120).stdout
    cpu = mem = 0
    for doc in yaml.safe_load_all(rendered):
        if not doc or doc.get("kind") not in ("Deployment", "StatefulSet", "DaemonSet", "Job"):
            continue
        spec = doc["spec"]["template"]["spec"]
        requests = [c.get("resources", {}).get("requests", {}) for c in spec["containers"]]
        cpu += sum(_cpu_m(str(r.get("cpu", "0m"))) for r in requests)
        mem += sum(_mem_mi(str(r.get("memory", "0Mi"))) for r in requests)
    assert cpu <= 3200, f"{cpu}m CPU requested"
    assert mem <= 11 * 1024, f"{mem}Mi memory requested"


def test_make_deploy_takes_several_platform_files_in_order():
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "deploy", "PLATFORM_VALUES=values-aks.yaml values-small-node.yaml"],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    assert out.index("-f values-aks.yaml") < out.index("-f values-small-node.yaml"), out
