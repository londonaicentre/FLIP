# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Dry-run tests for the aws/eks Makefile (FLIP#1439): no target reads the hub, the API endpoint admits
the operator's IP only, and the FL kit Secret never holds the SuperLink's key. No AWS, Terraform,
kubectl or Helm runs."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make(*args: str, dry: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["make", *(["-n"] if dry else []), "-C", str(ROOT), *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def test_plan_names_the_cluster_after_the_kit_and_admits_the_operator_only():
    out = make("plan", "KIT=EKS1", "HUB=stag", "OPERATOR_IP=203.0.113.7").stdout
    assert "-var 'name=flip-stag-eks1'" in out
    assert "-var 'subnet_ids=[]'" in out, "no site subnets: the root creates its network"
    assert "-var 'operator_cidrs=[\"203.0.113.7/32\"]'" in out


def test_site_subnets_are_passed_as_a_list():
    out = make("plan", "KIT=EKS1", "HUB=stag", "OPERATOR_IP=203.0.113.7", "SUBNET_IDS=subnet-a,subnet-b").stdout
    assert re.search(r"-var 'subnet_ids=\[\"subnet-a\", ?\"subnet-b\",?\]'", out), out


def test_destroy_never_looks_the_operator_up():
    out = make("destroy", "KIT=EKS1", "HUB=stag").stdout
    assert "192.0.2.1/32" in out
    assert "ipify" not in out


def test_a_kit_that_is_not_a_code_stops_every_target():
    result = make("init", "KIT=EKS1 HUB=stag", "STATE_BUCKET=b")
    assert result.returncode != 0
    assert "is not a kit code" in result.stderr


def test_join_needs_a_tarball():
    result = make("join", "KIT=EKS1", "HUB=stag", dry=False)
    assert result.returncode != 0
    assert "TARBALL=" in result.stdout + result.stderr


def test_join_records_the_eks_overlays_in_the_kit():
    out = make("join", "KIT=EKS1", "HUB=stag", "TARBALL=kit.tar.gz", "MOCK_PACS=1").stdout
    overlays = "values-eks.yaml values-small-node.yaml ../terraform/azure/aks/values-mock-pacs.yaml"
    assert f'K8S_PLATFORM_VALUES=%s\\n\' "{overlays}"' in out


def test_deploy_takes_the_overlays_join_recorded():
    """A redeploy that passed the default overlays would drop the ones join was given."""
    out = make("deploy", "KIT=EKS1", "HUB=stag").stdout
    assert "PLATFORM_VALUES" not in out
    assert "KUBE_CONTEXT=flip-stag-eks1" in out


def test_flower_kit_secret_holds_the_ca_and_credential_only(tmp_path):
    kit = tmp_path / "fl-kit" / "net-1"
    (kit / "certificates").mkdir(parents=True)
    (kit / "keys").mkdir()
    for f in (
        "certificates/ca.crt",
        "certificates/server.key",
        "keys/supernode_credentials_3",
        "keys/supernode_credentials_3.pub",
    ):
        (kit / f).write_text("x")
    result = make("_fl-kit", f"KIT_DIR={tmp_path}", "SLOT=Trust_3", dry=False)
    assert result.returncode == 0, result.stderr
    secret = sorted(str(p.relative_to(tmp_path / "fl-kit-secret")) for p in (tmp_path / "fl-kit-secret").rglob("*"))
    assert secret == ["certificates", "certificates/ca.crt", "keys", "keys/supernode_credentials_3"]


def test_no_target_reads_a_hub_env_file():
    text = (ROOT / "Makefile").read_text()
    assert "MAIN_ENV_FILE" not in text
    assert "HUB_ENV_FILE" not in text


def test_undeploy_deletes_the_namespace_and_destroy_refuses_left_volumes():
    """The chart's EBS volumes belong to the CSI driver, not Terraform: they outlive a bare destroy."""
    assert "delete namespace flip-trust" in make("undeploy", "KIT=EKS1", "HUB=stag").stdout
    destroy = make("destroy", "KIT=EKS1", "HUB=stag").stdout
    assert "kubernetes.io/created-for/pvc/namespace,Values=flip-trust" in destroy
    assert destroy.index("describe-volumes") < destroy.index("terraform destroy")


def test_undeploy_asks_before_it_deletes():
    out = make("undeploy", "KIT=EKS1", "HUB=stag").stdout
    assert "on context flip-stag-eks1? Type yes to continue" in out
    assert out.index("Type yes") < out.index("undeploy || true")


def test_undeploy_without_a_kit_stops():
    """With no NAME the chart's half would uninstall from kubectl's current context."""
    result = make("undeploy", dry=False)
    assert result.returncode != 0
    assert "KIT=<CODE>" in result.stdout + result.stderr
    assert "uninstall" not in result.stdout + result.stderr
