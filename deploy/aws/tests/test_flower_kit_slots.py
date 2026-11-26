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
#
"""A Flower kit slot is its SuperNode key pair in S3, on the hub and at the SuperLink alike (FLIP#1942).

Three places name the slot of ``keys/supernode_credentials_<N>{,.pub}`` as ``Trust_<N>``: flip-api (the pool
it claims from), ``add_fl_kits_flower.sh`` (the names it mints) and ``register-supernode-keys.sh`` (the name
the SuperLink knows the node by). The ECS registration task therefore carries no slot list of its own, and
is replayed whenever Terraform replaces the SuperLink, which keeps registrations in memory.
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

AWS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AWS_DIR.parents[1]
ECS_FLOWER = strip_comments((AWS_DIR / "ecs_flower.tf").read_text())
EFS_PROVISION = strip_comments((AWS_DIR / "ecs_efs_provision.tf").read_text())
LOCALS = strip_comments((AWS_DIR / "locals.tf").read_text())
ADD_KITS = (AWS_DIR / "scripts" / "add_fl_kits_flower.sh").read_text()
REGISTER = (REPO_ROOT / "fl-services" / "flower" / "register-supernode-keys.sh").read_text()
RESOLVER = (REPO_ROOT / "flip-api" / "src" / "flip_api" / "db" / "seed" / "fl_kit_slots.py").read_text()


def test_the_three_sides_name_a_key_and_its_slot_alike():
    assert 'slot = f"Trust_{number}"' in RESOLVER
    assert r"keys/(supernode_credentials_(\d+)(?:\.pub)?)$" in RESOLVER
    assert 'MINT_NAMES+=("Trust_$((max_num + i))")' in ADD_KITS
    assert 'key="supernode_credentials_${number}"' in ADD_KITS
    assert r"/supernode_credentials_([0-9]+)\.pub$ ]]" in REGISTER
    assert 'trust_names+=("Trust_${BASH_REMATCH[1]}")' in REGISTER


def test_the_registration_task_takes_its_names_from_the_keys():
    task = hcl_block(ECS_FLOWER, 'resource "aws_ecs_task_definition" "flower_register_supernode_keys"')
    assert "TRUST_NAMES" not in task
    assert "FL_KIT_SLOT_NAMES" not in ECS_FLOWER


def test_a_new_superlink_or_fl_api_task_definition_replays_the_registration():
    """The SuperLink runs without --database and the fl-api keeps its node → trust name map in memory: a
    replaced task of either has lost what the registration wrote."""
    superlink = hcl_block(
        strip_comments((AWS_DIR / "ecs_tasks.tf").read_text()), 'resource "aws_ecs_task_definition" "fl_server_net_1"'
    )
    assert "--database" not in superlink
    register = hcl_block(ECS_FLOWER, 'resource "null_resource" "flower_register_supernode_keys"')
    assert re.search(r"superlink\s*=\s*aws_ecs_task_definition\.fl_server_net_1\[0\]\.arn", register)
    assert re.search(r"fl_api\s*=\s*aws_ecs_task_definition\.fl_api_net_1\[0\]\.arn", register)


def test_flip_api_is_told_the_flower_kit_prefix_on_a_flower_hub_only():
    flip_api = hcl_block(hcl_block(LOCALS, "ecs_task_env = {"), "flip_api = merge(")
    uri = r'FLOWER_KITS_S3_URI\s*=\s*var\.fl_backend == "flower" \? local\.flower_kits_base_s3 : ""'
    assert re.search(uri, flip_api)
    assert 'flower_creds_base_s3 = "${local.flower_kits_base_s3}/net-1"' in re.sub(r" +", " ", EFS_PROVISION)


def test_add_fl_kits_copies_keys_to_the_hub_with_the_provisioning_tasks_own_sync():
    """The override is the task's key sync alone: additive, and public keys only."""
    sync = '/mnt/flower-keys/ --exclude "*" --include "*.pub"'
    assert f'aws s3 sync "$S3_BASE/keys/" {sync}' in EFS_PROVISION
    assert "aws s3 sync ${BASE_S3}/${NET}/keys/ " + sync.replace('"', "'") in ADD_KITS
    assert "--delete" not in re.sub(r"(?m)^\s*#.*$", "", ADD_KITS)


def test_add_fl_kits_runs_the_task_definitions_terraform_owns():
    families = dict(re.findall(r"run_one_shot (\S+) (\S+) ", re.sub(r"(?m)^\s*#.*$", "", ADD_KITS)))
    assert families == {
        "efs-provision-certs": "provision-efs-certs",
        "flower-register-supernode-keys": "register-supernode-keys",
    }
    for source, family, container in (
        (EFS_PROVISION, "efs-provision-certs", "provision-efs-certs"),
        (ECS_FLOWER, "flower-register-supernode-keys", "register-supernode-keys"),
    ):
        assert re.search(rf'family\s*=\s*"{family}"', source)
        assert re.search(rf'name\s*=\s*"{container}"', source)


def test_add_fl_kits_reads_the_registration_tasks_own_report():
    """add-fl-kits releases a slot only on the registration task's own log lines, read from where the
    task definition sends them: a rename on either side would stop every run."""
    task = hcl_block(ECS_FLOWER, 'resource "aws_ecs_task_definition" "flower_register_supernode_keys"')
    assert "awslogs-group         = aws_cloudwatch_log_group.ecs_fl_server_net_1.name" in task
    assert 'awslogs-stream-prefix = "register-supernode-keys"' in task
    ecs = strip_comments((AWS_DIR / "ecs.tf").read_text())
    log_group = hcl_block(ecs, 'resource "aws_cloudwatch_log_group" "ecs_fl_server_net_1"')
    assert 'name              = "/ecs/fl-server-net-1"' in log_group
    assert 'REGISTER_LOG_GROUP="/ecs/fl-server-${NET}"' in ADD_KITS
    assert '"register-supernode-keys/register-supernode-keys/${TASK_ID}"' in ADD_KITS

    assert 'echo "All SuperNode keys registered successfully."' in REGISTER
    assert '*"All SuperNode keys registered successfully."*' in ADD_KITS
    assert 'echo "    OK: ${pub_key##*/} node_id=${node_id}"' in REGISTER
    assert '*"OK: supernode_credentials_${number}.pub node_id="*' in ADD_KITS

    # A mapping-only failure: exit 3 and an "ERROR: FL API at" line, which add-fl-kits accepts and reports.
    assert REGISTER.count("exit 3") == 2
    assert REGISTER.count('echo "ERROR: FL API at ${fl_api_address} ') == 2
    assert 'run_one_shot flower-register-supernode-keys register-supernode-keys "fl-api-${NET}" "" 3' in ADD_KITS
    assert '"${TASK_EXIT_CODE}" == "3" || "${register_log}" == *"ERROR: FL API at "*' in ADD_KITS


def test_the_copy_tasks_command_override_runs_in_a_shell():
    """add-fl-kits overrides the copy task's command with one shell string: the task must run it with
    a shell entrypoint, not the image's `aws`."""
    task = hcl_block(EFS_PROVISION, 'resource "aws_ecs_task_definition" "efs_provision"')
    assert 'entryPoint = ["/bin/sh", "-c"]' in task
