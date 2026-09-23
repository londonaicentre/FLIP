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
"""A cloud Trust EC2 can be a GPU host, and every other trust EC2 stays exactly as it was.

The host shape is three Terraform inputs (``trust_instance_type``, ``trust_ami_ssm_parameter``,
``trust_root_volume_size``) whose defaults reproduce the historical t3.xlarge / stock Ubuntu /
100 GiB host, so a plan with none of them set is a no-op. They reach CI as OPTIONAL keys, so
every Terraform workflow must pass them through or a GitHub-environment value is silently dropped.

On the host, ``up-trust-ec2`` pins the fl-client to CPU unless ``TRUST_EC2_NUM_GPUS`` opts in. It is
deliberately NOT keyed on the kit's ``NUM_AVAILABLE_GPUS``: the kit template seeds that to 1, so
keying on it would start every existing CPU EC2 trust with a GPU reservation it cannot satisfy.
"""

import re
from pathlib import Path

import pytest
from make_probe import probe_make
from tf_source import hcl_block, strip_comments

AWS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AWS_DIR.parents[2]
TRUST_DIR = REPO_ROOT / "trust"
WORKFLOWS = [REPO_ROOT / ".github" / "workflows" / f"terraform_{name}.yml" for name in ("plan", "apply", "drift")]

#: Terraform variable → the env-file / CI key that sets it.
SHAPE_KEYS = {
    "trust_instance_type": "TRUST_INSTANCE_TYPE",
    "trust_ami_ssm_parameter": "TRUST_AMI_SSM_PARAMETER",
    "trust_root_volume_size": "TRUST_ROOT_VOLUME_SIZE",
}
#: What the trust host was before these inputs existed — the defaults must keep it.
HISTORICAL_DEFAULTS = {
    "trust_instance_type": '"t3.xlarge"',
    "trust_ami_ssm_parameter": '"/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"',
    "trust_root_volume_size": "100",
}


def _default(tf_file: Path, variable: str) -> str:
    body = hcl_block(strip_comments(tf_file.read_text()), f'variable "{variable}"')
    match = re.search(r"^\s*default\s*=\s*(.+?)\s*$", body, re.MULTILINE)
    assert match, f"variable {variable} in {tf_file.name} has no default"
    return match.group(1)


@pytest.mark.parametrize("variable", sorted(SHAPE_KEYS))
def test_root_default_keeps_the_historical_host(variable: str) -> None:
    assert _default(AWS_DIR / "variables.tf", variable) == HISTORICAL_DEFAULTS[variable]


def test_module_defaults_agree_with_the_root() -> None:
    module_vars = AWS_DIR / "modules" / "trust_ec2" / "variables.tf"
    assert _default(module_vars, "ami_ssm_parameter") == HISTORICAL_DEFAULTS["trust_ami_ssm_parameter"]
    assert _default(module_vars, "root_volume_size") == HISTORICAL_DEFAULTS["trust_root_volume_size"]


def test_trust_module_takes_the_shape_from_variables_not_literals() -> None:
    call = hcl_block(strip_comments((AWS_DIR / "main.tf").read_text()), 'module "trust_ec2"')
    assert re.search(r"^\s*instance_type\s*=\s*var\.trust_instance_type\s*$", call, re.MULTILINE)
    assert re.search(r"^\s*ami_ssm_parameter\s*=\s*var\.trust_ami_ssm_parameter\s*$", call, re.MULTILINE)
    assert re.search(r"^\s*root_volume_size\s*=\s*var\.trust_root_volume_size\s*$", call, re.MULTILINE)

    module = strip_comments((AWS_DIR / "modules" / "trust_ec2" / "main.tf").read_text())
    assert "name = var.ami_ssm_parameter" in module
    assert "volume_size           = var.root_volume_size" in module
    assert "/aws/service/canonical" not in module, "the AMI path must come from the variable, not a literal"


@pytest.mark.parametrize(("variable", "key"), sorted(SHAPE_KEYS.items()))
def test_makefile_exports_each_key_only_when_set(variable: str, key: str) -> None:
    makefile = (AWS_DIR / "Makefile").read_text()
    # Guarded, so an unset key leaves variables.tf's default in charge rather than exporting "".
    assert f"ifneq ($({key}),)\nexport TF_VAR_{variable}=${{{key}}}\nendif" in makefile


@pytest.mark.parametrize("key", sorted(SHAPE_KEYS.values()))
def test_ci_composes_and_passes_each_key(key: str) -> None:
    script = (AWS_DIR / "scripts" / "compose-ci-env.sh").read_text()
    optional = script[script.index("OPTIONAL_KEYS=(") :]
    optional = optional[: optional.index("\n)")]
    assert re.search(rf"^\s*{key}\s*$", optional, re.MULTILINE), f"{key} missing from OPTIONAL_KEYS"
    for workflow in WORKFLOWS:
        assert f"{key}: ${{{{ vars.{key} }}}}" in workflow.read_text(), f"{workflow.name} does not pass {key}"


def _ec2_gpu(variables: dict[str, str]) -> tuple[str, str]:
    # A KIT with no kit file keeps the -include inert; FL_BACKEND must then be supplied.
    base = {"PROD": "stag", "KIT": "ZZPROBE", "TRUST_NUM": "1", "FL_BACKEND": "nvflare"}
    num, override = probe_make(TRUST_DIR, ["$(EC2_NUM_GPUS)", "$(EC2_GPU_OVERRIDE)"], base | variables)
    return num, override


def test_up_trust_ec2_defaults_to_cpu() -> None:
    assert _ec2_gpu({}) == ("0", "")


def test_kit_gpu_count_alone_does_not_enable_the_overlay() -> None:
    # The kit template's NUM_AVAILABLE_GPUS=1 must not flip an existing CPU EC2 trust onto the GPU overlay.
    assert _ec2_gpu({"NUM_AVAILABLE_GPUS": "1"}) == ("0", "")


def test_explicit_opt_in_enables_the_overlay() -> None:
    assert _ec2_gpu({"TRUST_EC2_NUM_GPUS": "1"}) == ("1", "-f deploy/compose_trust.production.gpu.yml")


def test_up_trust_ec2_recipe_uses_the_opt_in() -> None:
    makefile = (TRUST_DIR / "Makefile").read_text()
    recipe = makefile[makefile.index("\nup-trust-ec2:") :]
    recipe = recipe[: recipe.index("\n\n")]
    assert "NUM_AVAILABLE_GPUS=$(EC2_NUM_GPUS)" in recipe
    assert "$(EC2_GPU_OVERRIDE)" in recipe
    assert "NUM_AVAILABLE_GPUS=0" not in recipe
