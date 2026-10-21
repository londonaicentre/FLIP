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
"""Dry-run tests for the aws-ec2 Makefile (FLIP#1528): site targets need the kit file only, the
hub-admin preset is the only place the hub's values come in, and the first install seeds with the
stack stopped. `make -n` against a throwaway kit; no AWS, Terraform or Ansible runs."""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KIT = (
    "FL_BACKEND=nvflare\nFL_KIT_SLOT_NUMBER=3\nFL_EDGE_PORT=8003\nDOCKER_TAG=sha-abc1234\n"  # pragma: allowlist secret
    "OMOP_POSTGRES_PASSWORD=kit-pw\n"  # pragma: allowlist secret
)
HUB_ENV = (
    "AWS_PROFILE=lza-stag\nAWS_REGION=eu-west-2\nFLIP_TFSTATE_BUCKET_NAME=hub-state\n"  # pragma: allowlist secret
    "AICENTRE_BUCKET_NAME=hub-kits\nFLARE_KIT_DATE=20260318\n"  # pragma: allowlist secret
    "FLOWER_KIT_DATE=20260401\nLZA_VPC_NAME=stag-vpc\n"  # pragma: allowlist secret
)


@pytest.fixture
def kit(tmp_path: Path) -> Path:
    path = tmp_path / ".env.TEST.stag"
    path.write_text(KIT)
    return path


def make(target: str, kit: Path, *extra: str, dry: bool = True) -> subprocess.CompletedProcess[str]:
    args = ["make", *(["-n"] if dry else []), "-C", str(ROOT), target, "KIT=TEST", "HUB=stag", f"KIT_FILE={kit}"]
    return subprocess.run([*args, *extra], capture_output=True, text=True, timeout=60)


def test_site_plan_reads_no_hub_value(kit):
    out = make("plan", kit).stdout
    assert "-var 'name=flip-stag-test'" in out
    assert "-var 'fl_server_ports=[8003]'" in out, "the FL port comes from the kit"
    assert "-var 'subnet_id='" in out
    assert "-var 'kit_bucket_name='" in out
    assert "kit_slot" not in out, "no kit bucket, no slot-scoped S3 read"
    assert "hub-" not in out
    assert "stag-vpc" not in out


def test_hub_preset_passes_the_hubs_values_in(kit, tmp_path):
    hub_env = tmp_path / ".env.stag"
    hub_env.write_text(HUB_ENV)
    preset = [f"HUB_ENV_FILE={hub_env}", "SUBNET_ID=subnet-0hub", "KIT_KMS_KEY_ARN=arn:kms", "BOUNDARY_ARN=arn:b"]
    out = make("plan", kit, *preset).stdout
    for var in ("subnet_id=subnet-0hub", "kit_bucket_name=hub-kits", "kit_slot=3", "kit_kms_key_arn=arn:kms"):
        assert f"-var '{var}'" in out, var
    assert "bucket=hub-state" in make("init", kit, f"HUB_ENV_FILE={hub_env}").stdout


def test_preset_looks_the_hub_subnet_up_by_its_lza_tag():
    text = (ROOT / "Makefile").read_text()
    assert "Name=tag:Name,Values=$(call hub_get,LZA_VPC_NAME)-app-*" in text
    assert "sort(Subnets[].SubnetId)[0]" in text, "the same subnet the hub's own trust EC2 uses"


def test_plan_refuses_a_kit_the_hub_has_not_registered(tmp_path):
    unregistered = tmp_path / ".env.TEST.stag"
    unregistered.write_text("FL_BACKEND=nvflare\n")
    result = make("plan", unregistered, dry=False)
    assert result.returncode != 0
    assert "register the trust first" in result.stdout + result.stderr


def test_provision_without_a_kit_bucket_expects_an_out_of_band_kit(kit):
    out = make("provision", kit).stdout
    assert "aws-ec2.yml" in out
    assert "-e fl_kit_source=precreate" in out
    assert "--skip-tags omop-vocab,seed" in out
    assert "-e node_alias=flip-stag-test" in out
    assert "-e trust_num=3" in out
    assert "-e fl_kit_precreate_slot=Trust_3" in out, "the tree is laid down for the kit's own slot"


def test_a_local_kit_dir_is_copied_over(kit, tmp_path):
    out = make("stage-kit", kit, f"KIT_SRC={tmp_path}").stdout
    assert "--tags flkit" in out
    assert f"-e fl_kit_source=local -e fl_kit_local_src={tmp_path}" in out


def test_stage_kit_needs_a_bucket_or_a_local_dir(kit):
    result = make("stage-kit", kit, dry=False)
    assert result.returncode != 0
    assert "KIT_SRC=" in result.stdout + result.stderr


def test_seed_without_a_kit_bucket_skips_the_vocabulary(kit, tmp_path):
    assert "--tags data --skip-tags omop-vocab" in make("seed", kit).stdout
    hub_env = tmp_path / ".env.stag"
    hub_env.write_text(HUB_ENV)
    with_bucket = make("seed", kit, f"HUB_ENV_FILE={hub_env}").stdout
    assert "--skip-tags omop-vocab" not in with_bucket
    assert "-e vocab_s3_bucket=hub-kits" in with_bucket


def test_deploy_refuses_relative_host_paths(tmp_path):
    """A relative path resolves to the operator's checkout over the ssh docker context (21 Oct live run)."""
    relative = tmp_path / ".env.TEST.stag"
    relative.write_text(KIT + "ORTHANC_STORAGE_DIR=./orthanc/orthanc-storage-trust1\nXNAT_DATA_DIR=/opt/flip/x\n")
    for target in ("deploy", "upgrade"):
        result = make(target, relative, dry=False)
        assert result.returncode != 0, target
        assert "relative host paths for a remote node: ORTHANC_STORAGE_DIR." in result.stdout + result.stderr


def test_provision_closes_the_shared_ssh_connection(kit):
    """The play adds ubuntu to the docker group; a connection opened before keeps the old groups."""
    assert "ssh -O exit flip-stag-test" in make("provision", kit).stdout


def test_deploy_only_tolerates_an_existing_swarm(kit):
    out = make("deploy", kit).stdout
    assert "already part of a swarm" in out
    assert "swarm init 2>/dev/null ||" not in out, "a blanket fallback hides an unreachable daemon"


def test_an_unset_aws_profile_is_not_exported(kit):
    """An empty exported AWS_PROFILE makes the AWS CLI fail with "config profile () could not be found"."""
    probe = "__probe: ; @env | grep -c '^AWS_PROFILE=' || true\n"

    def exported(*extra: str) -> str:
        cmd = ["make", "-s", "-C", str(ROOT), "-f", "Makefile", "-f", "-", "__probe", "KIT=TEST", "HUB=stag"]
        env = {k: v for k, v in os.environ.items() if k != "AWS_PROFILE"}
        out = subprocess.run([*cmd, f"KIT_FILE={kit}", *extra], input=probe, capture_output=True, text=True, env=env)
        return out.stdout.strip()

    assert exported() == "0"
    assert exported("AWS_PROFILE=flip-trial") == "1"


def test_deploy_seeds_between_stopping_and_starting_the_stack(kit):
    lines = make("deploy", kit).stdout.splitlines()

    def first(needle: str) -> int:
        return next(i for i, line in enumerate(lines) if needle in line)

    down, seed, up = first("down-trust-ec2"), first(" seed KIT=TEST"), first("up-trust-ec2")
    assert down < seed < up, f"deploy order is down={down}, seed={seed}, up={up}"
    assert any("create-trust-network KIT=TEST" in line for line in lines), "the kit's own slot network"
    assert all("DOCKER_CONTEXT=flip-stag-test" in lines[i] for i in (down, up))


def test_no_target_reads_a_hub_env_file_unless_given_one():
    text = (ROOT / "Makefile").read_text()
    assert not re.search(r"^-?include .*MAIN_ENV_FILE", text, re.M)
    assert re.search(r"^ifneq \(\$\(HUB_ENV_FILE\),\)$", text, re.M), "the preset is opt-in"
