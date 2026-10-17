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
"""The trust on the AKS cluster: the azure targets that drive trust/deploy/helm (FLIP#1390).

The chart's own targets are replaced by `echo make`, so these read what would be handed to them.
"""

from __future__ import annotations

import json

import pytest
from conftest import AZURE_DIR, REPO_ROOT, run_make

HELM = f"make -C {REPO_ROOT}/trust/deploy/helm KUBE_CONTEXT=flipaz-aks NAMESPACE=flip-trust"


def test_deploy_layers_the_aks_overlays(tmp_path):
    result, _ = run_make(tmp_path, "aks-deploy", MAKE="echo make", KIT="Trust_3", PROD="lza-stag")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == (
        f"{HELM} deploy-trust-k8s KIT=Trust_3 PROD=lza-stag PLATFORM_VALUES=values-aks.yaml values-small-node.yaml"
    )


def test_mock_pacs_seeds_orthanc_last(tmp_path):
    result, _ = run_make(tmp_path, "aks-deploy", MAKE="echo make", KIT="Trust_3", PROD="lza-stag", MOCK_PACS="1")

    overlay = AZURE_DIR / "aks" / "values-mock-pacs.yaml"
    assert result.stdout.strip().endswith(f"values-small-node.yaml {overlay}"), result.stdout
    assert overlay.read_text().endswith("trustData:\n  seed:\n    orthanc: true\n")


@pytest.mark.parametrize("value", ["0", "false"])
def test_mock_pacs_off_values_add_no_overlay(tmp_path, value):
    result, _ = run_make(tmp_path, "aks-deploy", MAKE="echo make", KIT="Trust_3", PROD="lza-stag", MOCK_PACS=value)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "values-mock-pacs.yaml" not in result.stdout


@pytest.mark.parametrize("target", ["aks-deploy", "aks-join"])
def test_kit_and_env_are_required(tmp_path, target):
    result, calls = run_make(tmp_path, target, MAKE="echo make", KIT="Trust_3")

    assert result.returncode != 0
    assert "PROD=<env>" in result.stdout + result.stderr
    assert not calls
    assert "make -C" not in result.stdout


def test_join_installs_the_kit_then_its_secrets_then_deploys(tmp_path):
    tarball = tmp_path / "flip-trust-kit-Trust_3-20261009.tar.gz"
    result, _ = run_make(
        tmp_path, "-n", "aks-join", MAKE="echo make", TARBALL=str(tarball), KIT="Trust_3", PROD="lza-stag"
    )

    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    kit_dir = "build/kits/flip-trust-kit-Trust_3-20261009"
    steps = [
        f'install -m 0600 "{kit_dir}/.env.Trust_3" "{REPO_ROOT}/trust/.env.Trust_3.lza-stag"',
        "K8S_PLATFORM_VALUES=%s",
        f"--secrets-file {REPO_ROOT}/trust/deploy/helm/values-secrets.yaml",
        "kubectl --context flipaz-aks create namespace flip-trust",
        "make _aks-fl-kit",
        f'{HELM} kit-secret KIT_SRC="{AZURE_DIR}/{kit_dir}/fl-kit-secret"',
        "make aks-deploy KIT=Trust_3 PROD=lza-stag",
    ]
    positions = [out.find(s) for s in steps]
    assert -1 not in positions, (steps[positions.index(-1)], out)
    assert positions == sorted(positions), out


def _kit(root, files: dict[str, str]):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


FLOWER_TARBALL = {
    "fl-kit/net-1/certificates/ca.crt": "ca",
    "fl-kit/net-1/certificates/server.pem": "pem",
    "fl-kit/net-1/certificates/server.key": "superlink private key",
    "fl-kit/net-1/keys/supernode_credentials_3": "slot key",
    "fl-kit/net-1/keys/supernode_credentials_3.pub": "slot pub",
}


def test_a_flower_kit_secret_holds_only_what_the_supernode_reads(tmp_path):
    """The hub's tarball ships the whole certificates/ (with the SuperLink's server.key); kit-secret
    refuses that, so aks-join hands it ca.crt and the slot's credential only."""
    kit = tmp_path / "kit"
    _kit(kit, FLOWER_TARBALL)

    result, _ = run_make(tmp_path, "_aks-fl-kit", AKS_KIT_DIR=str(kit), KIT="Trust_3")

    assert result.returncode == 0, result.stdout + result.stderr
    secret = kit / "fl-kit-secret"
    staged = sorted(str(p.relative_to(secret)) for p in secret.rglob("*") if p.is_file())
    assert staged == ["certificates/ca.crt", "keys/supernode_credentials_3"]


def test_an_nvflare_kit_secret_is_the_slots_directory(tmp_path):
    kit = tmp_path / "kit"
    _kit(kit, {"fl-kit/net-1/services/Trust_3/startup/fed_client.json": "{}", "fl-kit/net-1/services/Trust_1/x": "no"})

    result, _ = run_make(tmp_path, "_aks-fl-kit", AKS_KIT_DIR=str(kit), KIT="Trust_3")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (kit / "fl-kit-secret/startup/fed_client.json").read_text() == "{}"
    assert not (kit / "fl-kit-secret/x").exists()


def _job(name: str, cpu_limit: str | None) -> dict:
    resources = {"limits": {"cpu": cpu_limit}} if cpu_limit else {}
    return {"metadata": {"name": name}, "spec": {"template": {"spec": {"containers": [{"resources": resources}]}}}}


JOBS = json.dumps({"items": [_job("old-1", "1"), _job("capped", None), _job("old-2", "1")]})


def test_clear_jobs_deletes_only_the_jobs_from_before_the_cap_after_a_yes(tmp_path):
    result, calls = run_make(tmp_path, "aks-clear-jobs", kubectl_jobs=JOBS, stdin="yes\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "old-1\nold-2\n2 Jobs from before the cap" in result.stdout, "the names come before the prompt"
    assert calls[-1] == "kubectl --context flipaz-aks delete job -n flip-trust old-1 old-2"


def test_clear_jobs_deletes_nothing_without_a_yes(tmp_path):
    result, calls = run_make(tmp_path, "aks-clear-jobs", kubectl_jobs=JOBS, stdin="no\n")

    assert result.returncode != 0
    assert not [c for c in calls if " delete " in c]


def test_clear_jobs_fails_when_the_cluster_cannot_be_read(tmp_path):
    result, calls = run_make(tmp_path, "aks-clear-jobs", kubectl_jobs="not json", stdin="yes\n")

    assert result.returncode != 0
    assert "No Container Service Jobs" not in result.stdout, "an unreadable answer is not an empty one"
    assert not [c for c in calls if " delete " in c]


@pytest.mark.parametrize("rg", ["flipaz-node-rg", "flipaz-aks-rg"])
def test_destroy_bootstrap_refuses_while_the_node_or_the_cluster_exists(tmp_path, rg):
    result, calls = run_make(tmp_path, "destroy-bootstrap", existing_rg=rg)

    assert result.returncode != 0
    assert f"{rg} still exists" in result.stdout + result.stderr
    assert not [c for c in calls if c.startswith("terraform -chdir=bootstrap destroy")]
