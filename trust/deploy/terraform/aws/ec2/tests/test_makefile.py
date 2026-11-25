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
"""Dry-run tests for the aws-ec2 Makefile (FLIP#1528): site targets need the kit file only and the
first install seeds with the stack stopped. `make -n` against a throwaway kit; no AWS, Terraform or Ansible runs."""

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


def kit_with(tmp_path: Path, extra: str) -> Path:
    path = tmp_path / ".env.TEST.stag"
    path.write_text(KIT + extra)
    return path


@pytest.fixture
def kit(tmp_path: Path) -> Path:
    return kit_with(tmp_path, "")


def make(
    target: str,
    kit: Path,
    *extra: str,
    dry: bool = True,
    env: dict[str, str] | None = None,
    state_bucket: str | None = "",
    state_name: str = "flip-stag-test",
    state_key: str = "",
    applied: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a target. `terraform` is a stub that logs its arguments beside the kit and answers `output` from the
    root's state: the node `state_name`, its kit bucket `state_bucket` and KMS key `state_key` (bucket None: a state
    from before the root recorded them), and an instance only when `applied`."""
    stub = kit.parent / "tf-stub" / "terraform"
    stub.parent.mkdir(exist_ok=True)
    recorded = "exit 1" if state_bucket is None else f"printf %s '{state_bucket}'"
    named = "exit 1" if state_bucket is None else f"printf %s '{state_name}'"
    key = "exit 1" if state_bucket is None else f"printf %s '{state_key}'"
    stub.write_text(
        f'#!/bin/sh\necho "$*" >> {kit.parent}/terraform.calls\ncase "$*" in\n'
        f"  *node_name) {named} ;;\n  *kit_bucket_name) {recorded} ;;\n  *kit_kms_key_arn) {key} ;;\n"
        f"  *instance_id) exit {0 if applied else 1} ;;\nesac\n"
    )
    stub.chmod(0o755)
    env = dict(os.environ if env is None else env)
    env["PATH"] = f"{stub.parent}{os.pathsep}{env['PATH']}"
    # GNU make 4 prints "Entering directory '…/terraform/aws/ec2'" for -C; keep stdout to the recipes.
    args = ["make", "--no-print-directory", *(["-n"] if dry else []), "-C", str(ROOT), target]
    args += ["KIT=TEST", "HUB=stag", f"KIT_FILE={kit}"]
    return subprocess.run([*args, *extra], capture_output=True, text=True, timeout=60, env=env)


def stub_tools(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Stand-ins for the tools a real run would reach: a guard that lets it through records the call."""
    bin_dir, calls = tmp_path / "bin", tmp_path / "calls"
    bin_dir.mkdir()
    for tool in ("uv", "docker", "ssh"):
        (bin_dir / tool).write_text(f"#!/bin/sh\necho {tool} >> {calls}\nexit 97\n")
        (bin_dir / tool).chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, calls


def test_site_plan_reads_no_hub_value(kit):
    out = make("plan", kit).stdout
    assert "-var 'name=flip-stag-test'" in out
    assert "-var 'fl_server_ports=[8003]'" in out, "the FL port comes from the kit"
    assert "-var 'subnet_id='" in out
    assert "-var 'kit_bucket_name='" in out
    assert "kit_slot" not in out, "no kit bucket, no slot-scoped S3 read"
    assert "hub-" not in out
    assert "stag-vpc" not in out


def test_private_ip_reaches_the_plan(kit):
    assert "-var 'private_ip='" in make("plan", kit).stdout, "unset, AWS picks the address"
    out = make("plan", kit, "SUBNET_ID=subnet-0hub", "PRIVATE_IP=10.8.0.200").stdout
    assert "-var 'subnet_id=subnet-0hub'" in out
    assert "-var 'private_ip=10.8.0.200'" in out


def test_plan_refuses_a_kit_the_hub_has_not_registered(tmp_path):
    unregistered = tmp_path / ".env.TEST.stag"
    unregistered.write_text("FL_BACKEND=nvflare\n")
    result = make("plan", unregistered, dry=False)
    assert result.returncode != 0
    assert "register the trust first" in result.stdout + result.stderr


def test_a_kit_that_is_not_a_code_stops_every_target(kit):
    """zsh does not split `make init $V`, so KIT once held every assignment and REGION kept its default."""
    result = make("init", kit, "KIT=TEST HUB=stag REGION=eu-north-1", "STATE_BUCKET=b")
    assert result.returncode != 0
    assert "is not a kit code" in result.stderr
    assert "terraform" not in result.stdout


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


def test_seed_without_a_kit_bucket_skips_the_vocabulary(kit):
    assert "--tags data --skip-tags omop-vocab" in make("seed", kit).stdout, "a node applied with no bucket"
    with_bucket = make("seed", kit, "KIT_BUCKET=hub-kits").stdout
    assert "--skip-tags omop-vocab" not in with_bucket
    assert "-e vocab_s3_bucket=hub-kits" in with_bucket


def test_a_trust_with_its_own_data_gets_the_vocabulary_and_no_sample_data(tmp_path):
    """TRUST_DATA_SEED=false (FLIP#1687): neither seed nor deploy loads the sample data into the trust's stores."""
    own = tmp_path / ".env.TEST.stag"
    own.write_text(KIT + "TRUST_DATA_SEED=false  # our own OMOP and PACS\n")
    with_bucket = make("seed", own, "KIT_BUCKET=hub-kits").stdout
    assert "--tags data --skip-tags seed" in with_bucket, with_bucket
    assert "-e vocab_s3_bucket=hub-kits" in with_bucket
    for out in (make("seed", own).stdout, make("deploy", own).stdout):
        assert "ansible-playbook" not in out, out
        assert "nothing to load" in out, out
    assert "--skip-tags seed" in make("deploy", own, "KIT_BUCKET=hub-kits").stdout


def test_a_mistyped_sample_data_setting_stops_seed_and_deploy_before_they_start(tmp_path):
    bad = tmp_path / ".env.TEST.stag"
    bad.write_text(KIT + "TRUST_DATA_SEED=no\n")
    # A real run: a guard that lets it through records the call instead of driving Ansible or a docker context.
    env, calls = stub_tools(tmp_path)
    for target in ("seed", "deploy"):
        result = make(target, bad, dry=False, env=env)
        assert result.returncode != 0, target
        assert "TRUST_DATA_SEED must be true or false" in result.stdout + result.stderr, result.stdout
        assert not calls.exists(), f"{target} reached {calls.read_text()}"


def test_deploy_refuses_relative_host_paths(tmp_path):
    """A relative path resolves to the operator's checkout over the ssh docker context (21 Oct live run)."""
    relative = tmp_path / ".env.TEST.stag"
    relative.write_text(KIT + "ORTHANC_STORAGE_DIR=./orthanc/orthanc-storage-trust1\nXNAT_DATA_DIR=/opt/flip/x\n")
    for target in ("deploy", "upgrade"):
        result = make(target, relative, dry=False)
        assert result.returncode != 0, target
        assert "relative host paths for a remote node: ORTHANC_STORAGE_DIR." in result.stdout + result.stderr


NODE_DIRS = (
    "OMOP_DATA_DIR=/opt/flip/omop/db_data\n"
    "ORTHANC_STORAGE_DIR=/opt/flip/orthanc/orthanc-storage\n"  # pragma: allowlist secret
    "BASE_IMAGES_DOWNLOAD_DIR=/opt/flip/data/trust-3\n"  # pragma: allowlist secret
    "FL_KIT_DIR=/opt/flip/fl-kit\n"
)


@pytest.mark.parametrize(
    "line",
    [
        "OMOP_DATA_DIR=/data/flip/omop/db_data",
        "ORTHANC_STORAGE_DIR=/data/flip/orthanc",  # pragma: allowlist secret
        "BASE_IMAGES_DOWNLOAD_DIR=/opt/flip/data/trust-1",  # pragma: allowlist secret
    ],
)
def test_deploy_refuses_a_path_the_node_is_not_provisioned_with(tmp_path, line):
    """The play and the seed hold these paths as literals: compose elsewhere starts on empty dirs."""
    moved = kit_with(tmp_path, NODE_DIRS + line + "\n")
    env, calls = stub_tools(tmp_path)
    result = make("deploy", moved, dry=False, env=env)
    assert result.returncode != 0
    key = line.split("=")[0]
    expected = next(d for d in NODE_DIRS.splitlines() if d.startswith(key))
    assert f"     {expected}\n" in result.stdout, result.stdout
    assert not calls.exists(), f"deploy reached {calls.read_text()}"


OWN_DATA = (
    "TRUST_DATA_SEED=false\nOMOP_DATA_DIR=/data/flip/omop/db_data\n"  # pragma: allowlist secret
    "ORTHANC_STORAGE_DIR=/data/flip/orthanc\n"  # pragma: allowlist secret
    "BASE_IMAGES_DOWNLOAD_DIR=/data/flip/images\nFL_KIT_DIR=/data/flip/fl-kit\n"  # pragma: allowlist secret
)


def test_upgrade_leaves_a_live_nodes_own_paths_alone(tmp_path):
    """upgrade prepares, stages and seeds nothing: pointing a live kit at /opt/flip would start omop-db empty."""
    env, calls = stub_tools(tmp_path)
    result = make("upgrade", kit_with(tmp_path, OWN_DATA), dry=False, env=env)
    assert "host paths" not in result.stdout + result.stderr, result.stdout
    assert calls.read_text().startswith("docker"), "the guards passed and upgrade went on to the docker context"
    assert not (tmp_path / "terraform.calls").exists(), "upgrade reads no state"


def test_deploy_holds_a_data_path_only_where_it_fills_it(tmp_path):
    """With TRUST_DATA_SEED=false nothing seeds Orthanc, and OMOP gets only a kit bucket's vocabulary."""
    data = "TRUST_DATA_SEED=false\nFL_KIT_DIR=/opt/flip/fl-kit\nOMOP_DATA_DIR=/data/omop\n"  # pragma: allowlist secret
    own = kit_with(tmp_path, data + "ORTHANC_STORAGE_DIR=/data/orthanc\n")  # pragma: allowlist secret
    env, calls = stub_tools(tmp_path)
    no_bucket = make("deploy", own, dry=False, env=env)
    assert "names host paths" not in no_bucket.stdout, no_bucket.stdout
    assert calls.exists()
    calls.unlink()
    with_bucket = make("deploy", own, dry=False, env=env, state_bucket="hub-kits")
    assert with_bucket.returncode != 0
    assert "     OMOP_DATA_DIR=/opt/flip/omop/db_data\n" in with_bucket.stdout, "the vocabulary load writes there"
    assert "ORTHANC_STORAGE_DIR=" not in with_bucket.stdout
    assert not calls.exists()


def test_deploy_refuses_a_kit_without_an_fl_kit_dir(kit, tmp_path):
    """Compose has no default for FL_KIT_DIR: unset, the FL client mounts /net-1/… and starts without a kit."""
    env, calls = stub_tools(tmp_path)
    result = make("deploy", kit, dry=False, env=env)
    assert result.returncode != 0
    assert "has no FL_KIT_DIR" in result.stdout
    assert not calls.exists()


def test_deploy_takes_the_provisioned_paths_and_the_dirs_bring_up_prepares(tmp_path):
    """XNAT's tree and the membership store are created from the kit's own value at bring-up, wherever it points,
    and provision and stage-kit put the FL kit at the kit's FL_KIT_DIR."""
    elsewhere = (
        "XNAT_DATA_DIR=/data/xnat\nCOHORT_SNAPSHOT_STORAGE_DIR=/data/snapshots\n"  # pragma: allowlist secret
        "FL_KIT_DIR=/data/flip/fl-kit\n"
    )
    fine = kit_with(tmp_path, NODE_DIRS + elsewhere)
    env, calls = stub_tools(tmp_path)
    result = make("deploy", fine, dry=False, env=env)
    assert "names host paths" not in result.stdout + result.stderr
    assert calls.read_text().startswith("docker"), "the guards passed and deploy went on to the docker context"


def test_the_refused_paths_are_the_ones_the_roles_and_compose_use():
    """require_node_dirs states the provisioned paths; they are literals in three places that must agree."""
    makefile = (ROOT / "Makefile").read_text()
    trust = ROOT.parents[3]
    seed = (trust / "deploy/ansible/roles/flip_trust_seed/defaults/main.yml").read_text()
    compose = (trust / "deploy/compose/compose_trust.prod.yml").read_text()
    plays = (trust / "deploy/ansible/aws-ec2-host.yml").read_text()
    assert 'omop_dir: "/opt/flip/omop"' in seed
    assert (
        "{{ omop_dir }}/db_data:/var/lib/postgresql/data"
        in (trust / "deploy/ansible/roles/flip_trust_seed/tasks/main.yml").read_text()
    )
    assert 'orthanc_data_dir: "/opt/flip/orthanc/orthanc-storage"' in seed
    assert "'/opt/flip/data/trust-' ~ trust_num" in plays
    for default in (
        "${OMOP_DATA_DIR:-/opt/flip/omop/db_data}",
        "${ORTHANC_STORAGE_DIR:-/opt/flip/orthanc/orthanc-storage}",
        "${BASE_IMAGES_DOWNLOAD_DIR:-/opt/flip/data/trust-${FL_KIT_SLOT_NUMBER}}",
    ):
        assert default in compose
    for fixed in (
        "OMOP_DATA_DIR,/opt/flip/omop/db_data",
        "ORTHANC_STORAGE_DIR,/opt/flip/orthanc/orthanc-storage",  # pragma: allowlist secret
        "BASE_IMAGES_DOWNLOAD_DIR,/opt/flip/data/trust-$(KIT_SLOT)",
    ):
        assert fixed in makefile


@pytest.mark.parametrize("target", ["provision", "stage-kit"])
def test_the_fl_kit_is_staged_where_the_kit_file_says(tmp_path, target):
    """Compose mounts the kit from FL_KIT_DIR, so the play stages it there, not at the role's default."""
    moved = make(target, kit_with(tmp_path, "FL_KIT_DIR=/data/flip/fl-kit\n"), f"KIT_SRC={tmp_path}")
    assert "-e fl_kit_dir=/data/flip/fl-kit" in moved.stdout
    assert "fl_kit_dir" not in make(target, kit_with(tmp_path, ""), f"KIT_SRC={tmp_path}").stdout, "unset: the default"
    env, calls = stub_tools(tmp_path)
    relative = make(target, kit_with(tmp_path, "FL_KIT_DIR=./fl-kit\n"), f"KIT_SRC={tmp_path}", dry=False, env=env)
    assert relative.returncode != 0
    assert "FL_KIT_DIR=./fl-kit in .env.TEST.stag is relative" in relative.stdout
    assert not calls.exists()


def test_the_host_verbs_take_the_kit_bucket_the_node_was_applied_with(tmp_path):
    """Given to Terraform at plan, it used to be retyped on every host verb: a forgotten one skipped the
    vocabulary and switched provision to an empty pre-created kit tree, both with exit 0."""
    dated = kit_with(tmp_path, "FLARE_KIT_DATE=2026-11-01\nFLOWER_KIT_DATE=2026-10-01\n")
    for target in ("provision", "stage-kit"):
        out = make(target, dated, state_bucket="hub-kits").stdout
        assert "-e flip_aicentre_bucket=hub-kits -e fl_kit_date=2026-11-01" in out, target
        assert "fl_kit_source=precreate" not in out, target
    seed = make("seed", dated, state_bucket="hub-kits").stdout
    assert "--skip-tags omop-vocab" not in seed
    assert "-e vocab_s3_bucket=hub-kits" in seed
    once = ["output -raw node_name", "output -raw kit_bucket_name", "output -raw kit_kms_key_arn"]
    assert (tmp_path / "terraform.calls").read_text().splitlines() == once * 3, "one read a run"


def test_a_kit_bucket_on_the_command_line_wins_over_the_state(kit):
    assert "-e flip_aicentre_bucket=other" in make("provision", kit, "KIT_BUCKET=other", state_bucket="hub-kits").stdout
    none = make("provision", kit, "KIT_BUCKET=", state_bucket="hub-kits").stdout
    assert "-e fl_kit_source=precreate" in none, "an empty one says the node has no bucket"
    assert not (kit.parent / "terraform.calls").exists(), "a typed bucket needs no state read"


def test_a_local_kit_dir_wins_over_the_nodes_bucket(kit, tmp_path):
    out = make("stage-kit", kit, f"KIT_SRC={tmp_path}", state_bucket="hub-kits").stdout
    assert f"-e fl_kit_source=local -e fl_kit_local_src={tmp_path}" in out
    assert "flip_aicentre_bucket" not in out


@pytest.mark.parametrize("target", ["provision", "stage-kit", "seed", "deploy"])
def test_the_host_verbs_refuse_a_state_that_does_not_name_the_bucket(tmp_path, target):
    """A state from before the root recorded the bucket, read as "no bucket", would be the same silent switch."""
    path = kit_with(tmp_path, NODE_DIRS)
    env, calls = stub_tools(tmp_path)
    result = make(target, path, dry=False, env=env, state_bucket=None)
    assert result.returncode != 0
    assert "whether it reads a kit bucket" in result.stdout
    assert not calls.exists(), f"{target} reached {calls.read_text()}"
    assert make(target, path, "KIT_BUCKET=", f"KIT_SRC={tmp_path}", state_bucket=None).returncode == 0


@pytest.mark.parametrize("target", ["plan", "provision", "stage-kit", "seed", "deploy"])
def test_another_nodes_state_is_refused(tmp_path, target):
    """.terraform/ points at whichever node `make init` last named: its bucket is not this kit's node's."""
    path = kit_with(tmp_path, NODE_DIRS)
    env, calls = stub_tools(tmp_path)
    result = make(target, path, dry=False, env=env, state_bucket="other-hub-kits", state_name="flip-stag-other")
    assert result.returncode != 0
    assert "initialised for node flip-stag-other, not flip-stag-test" in result.stdout, result.stdout
    assert "make init KIT=TEST HUB=stag" in result.stdout
    assert not calls.exists(), f"{target} reached {calls.read_text()}"
    assert "terraform plan" not in result.stdout


@pytest.mark.parametrize("target", ["provision", "stage-kit", "seed", "deploy"])
def test_the_host_verbs_refuse_a_root_with_no_node(tmp_path, target):
    path = kit_with(tmp_path, NODE_DIRS)
    result = make(target, path, dry=False, env=stub_tools(tmp_path)[0], state_bucket=None, applied=False)
    assert result.returncode != 0
    assert "holds no node" in result.stdout


def test_a_later_plan_keeps_the_bucket_the_node_was_applied_with(kit):
    """Planned without them, the apply would drop the node's kit read (or only its kms:Decrypt, and the next
    stage-kit would wipe net-1/ and then be refused the objects) and empty what the state records."""
    arn = "arn:aws:kms:eu-west-2:000000000000:key/k"
    out = make("plan", kit, state_bucket="hub-kits", state_key=arn).stdout
    assert f"-var 'kit_bucket_name=hub-kits' -var 'kit_slot=3' -var 'kit_kms_key_arn={arn}'" in out
    assert "-var 'kit_kms_key_arn='" in make("plan", kit, state_bucket="hub-kits").stdout, "a bucket with no key"
    typed = make("plan", kit, "KIT_KMS_KEY_ARN=", state_bucket="hub-kits", state_key=arn).stdout
    assert "-var 'kit_bucket_name=hub-kits' -var 'kit_slot=3' -var 'kit_kms_key_arn='" in typed, "a typed key wins"
    typed = make("plan", kit, "KIT_BUCKET=", state_bucket="hub-kits", state_key=arn)
    first = make("plan", kit, state_bucket=None, applied=False)
    for why, out in (("a typed bucket reads neither from the state", typed), ("a first plan", first)):
        assert "-var 'kit_bucket_name='" in out.stdout, why
        assert "-var 'kit_kms_key_arn='" in out.stdout, why


def test_plan_refuses_a_node_whose_state_predates_the_record(kit, tmp_path):
    """It cannot tell a node with no bucket from one whose bucket was never recorded."""
    (tmp_path / "key.pub").write_text("ssh-ed25519 AAAA test\n")
    result = make("plan", kit, f"PUBLIC_KEY_FILE={tmp_path}/key.pub", dry=False, state_bucket=None)
    assert result.returncode != 0
    assert "whether it reads a kit bucket" in result.stdout
    assert "plan -out" not in (tmp_path / "terraform.calls").read_text()


@pytest.mark.parametrize("target", ["help", "upgrade", "destroy", "lint", "test"])
def test_only_plan_and_the_host_verbs_read_the_state(kit, target):
    """The read is lazy: nothing else runs terraform at parse time, under -n or for help."""
    assert make(target, kit, dry=target != "help").returncode == 0
    assert not (kit.parent / "terraform.calls").exists()


def test_the_seed_runs_the_images_at_the_kits_pins(kit, tmp_path):
    """Compose runs omop-db and orthanc at OMOP_DB_TAG / ORTHANC_TAG when the kit pins them, else DOCKER_TAG."""
    assert "-e omop_db_tag=sha-abc1234 -e orthanc_tag=sha-abc1234" in make("seed", kit).stdout
    pinned = make("seed", kit_with(tmp_path, "OMOP_DB_TAG=v0.15.0\nORTHANC_TAG=v0.14.0\n")).stdout
    assert "-e omop_db_tag=v0.15.0 -e orthanc_tag=v0.14.0" in pinned


def test_provision_closes_the_shared_ssh_connection(kit):
    """The play adds ubuntu to the docker group; a connection opened before keeps the old groups."""
    assert "ssh -O exit flip-stag-test" in make("provision", kit).stdout


def test_destroy_names_the_account_and_stops_without_a_typed_yes(kit, tmp_path):
    """The root volume holds the trust's data, so destroy names the account before Terraform runs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "aws").write_text("#!/bin/sh\necho 123456789012\n")
    (bin_dir / "terraform").write_text(f"#!/bin/sh\ntouch {tmp_path}/terraform-ran\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    args = ["make", "--no-print-directory", "-C", str(ROOT), "destroy", "KIT=TEST", "HUB=stag", f"KIT_FILE={kit}"]
    result = subprocess.run(args, input="no\n", capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode != 0
    assert "Target account: 123456789012" in result.stdout
    assert "cannot be recovered" in result.stdout
    assert not (tmp_path / "terraform-ran").exists()


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


def test_upgrade_keeps_the_data_and_the_fl_client_on_cpu(kit):
    """The release verb (FLIP#1204): no re-seed or stack restart, and NUM_AVAILABLE_GPUS=0 beats a
    template kit's 1, or the GPU overlay makes the recreated fl-client fail on this GPU-less node."""
    out = make("upgrade", kit).stdout.replace("\\\n", " ")
    call = next(line for line in out.splitlines() if "upgrade-trust " in line)
    assert "NUM_AVAILABLE_GPUS=0" in call, call
    assert not re.search(r"\bseed\b", out), "upgrade must not run seed"
    for forbidden in ("down-trust-ec2", "up-trust-ec2"):
        assert forbidden not in out, f"upgrade must not run {forbidden}"


def test_no_target_reads_a_hub_env_file():
    text = (ROOT / "Makefile").read_text()
    assert not re.search(r"^-?include .*MAIN_ENV_FILE", text, re.M)
    assert "HUB_ENV_FILE" not in text
