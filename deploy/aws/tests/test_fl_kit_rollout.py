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
"""A kit-date change reaches the running FL services, and a missing kit fails loudly (FLIP#1738).

The FL services read their kit from EFS once, at start. Before this, bumping FLOWER_KIT_DATE re-synced
EFS and re-ran the SuperNode key registration while the SuperLink kept serving the old certificates, so
the registration failed and the resource was left tainted. The four task-definition environment maps now
carry the kit's S3 source, so the change registers new revisions and rolls the services first; and the
EFS provisioning task refuses a kit prefix that does not exist instead of syncing nothing.
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

AWS_DIR = Path(__file__).resolve().parents[1]
LOCALS = strip_comments((AWS_DIR / "locals.tf").read_text())
EFS_PROVISION = strip_comments((AWS_DIR / "ecs_efs_provision.tf").read_text())
ECS_FLOWER = strip_comments((AWS_DIR / "ecs_flower.tf").read_text())

KIT_SOURCE_BY_MAP = {
    "fl_server": "local.fl_provision_base_s3",
    "fl_api": "local.fl_provision_base_s3",
    "fl_server_flower": "local.flower_creds_base_s3",
    "fl_api_flower": "local.flower_creds_base_s3",
}


def _env_map(name: str) -> str:
    ecs_task_env = hcl_block(LOCALS, "ecs_task_env = {")
    header = f"{name} = merge(" if name == "fl_server_flower" else f"{name} = {{"
    return hcl_block(ecs_task_env, header)


def test_every_fl_task_environment_carries_its_kit_source():
    for name, source in KIT_SOURCE_BY_MAP.items():
        assert re.search(rf"\bFL_KIT_SOURCE\s*=\s*{re.escape(source)}\b", _env_map(name)), name


def test_the_kit_source_is_the_prefix_the_provisioning_task_syncs():
    provisioning = hcl_block(EFS_PROVISION, 'resource "null_resource" "provision_efs_certs"')
    assert 'var.fl_backend == "flower" ? local.flower_creds_base_s3 : local.fl_provision_base_s3' in provisioning


def test_the_provisioning_task_refuses_a_missing_kit_prefix():
    task = hcl_block(EFS_PROVISION, 'resource "aws_ecs_task_definition" "efs_provision"')
    flower, flare = task.split(" : [", 1)
    guard = r'aws s3 ls "\$S3_BASE/{path}".*\|\| \{{.*{variable}.*exit 1'
    assert re.search(guard.format(path=r"certificates/server\.pem", variable="FLOWER_KIT_DATE"), flower, re.DOTALL)
    assert re.search(guard.format(path="fl-server-net-1/startup/", variable="FLARE_KIT_DATE"), flare, re.DOTALL)


def test_key_registration_runs_after_both_fl_services_are_stable():
    register = hcl_block(ECS_FLOWER, 'resource "null_resource" "flower_register_supernode_keys"')
    assert "aws_ecs_service.fl_server_net_1" in register
    assert "aws_ecs_service.fl_api_net_1" in register
    assert re.search(r"aws ecs wait services-stable.*--services fl-server-net-1 fl-api-net-1", register, re.DOTALL)
