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
"""The role that runs FL job code holds no participant credentials, and no ECS role reads the FLARE CA state.

The fl-server task runs researcher-supplied job code, so its role must not reach the kit prefixes of the
AICENTRE bucket (every participant's FL credentials), nor the results bucket, where it would reach every
project's results: a job uploads its own through URLs flip-api presigns. The one-shot EFS provisioning task
reads the hub's side of the kit under its own role, and the FLARE CA state lives under a prefix of its own that
operators read with their own credentials.
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

AWS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AWS_DIR.parents[2]
IAM_ECS = strip_comments((AWS_DIR / "iam_ecs.tf").read_text())
EFS_PROVISION = strip_comments((AWS_DIR / "ecs_efs_provision.tf").read_text())
LOCALS = strip_comments((AWS_DIR / "locals.tf").read_text())
TASK_DEFINITIONS = {
    name: strip_comments((AWS_DIR / name).read_text())
    for name in ("ecs_tasks.tf", "ecs_flower.tf", "ecs_efs_provision.tf")
}

CA_STATE_PREFIX = "fl-flare-ca-state"
PROVISION_ROLE = "aws_iam_role.ecs_fl_kit_provision_task.arn"


def _policy(name: str) -> str:
    return hcl_block(IAM_ECS, f'data "aws_iam_policy_document" "{name}"')


def _statement(policy: str, sid: str) -> str:
    return next(body for body in re.split(r"\n  statement \{", policy) if f'sid = "{sid}"' in re.sub(r" +", " ", body))


def test_the_fl_server_role_reaches_nothing_in_the_kit_bucket():
    policy = _policy("ecs_fl_server_task")
    assert "aicentre_bucket" not in policy
    assert "participant-kits" not in policy


def test_the_fl_server_role_reaches_no_bucket_and_no_key():
    """A job uploads its results through URLs flip-api presigns, so the role that runs it needs neither."""
    policy = _policy("ecs_fl_server_task")
    assert not re.search(r'"(s3|kms):', policy)
    assert re.findall(r'sid\s*=\s*"(\w+)"', policy) == ["EcsExecSsmMessages"]


def test_the_fl_server_tasks_are_not_told_a_bucket():
    task_env = hcl_block(LOCALS, "ecs_task_env = {")
    for name in ("fl_server = {", "fl_server_flower = merge({"):
        assert not re.search(r"BUCKET|AWS_", hcl_block(task_env, name)), name


def test_flip_api_can_write_the_results_it_presigns_uploads_for():
    """s3:PutObject covers the multipart calls; the bucket's SSE-KMS key needs Decrypt and GenerateDataKey."""
    policy = _policy("ecs_flip_api_task")
    buckets = _statement(policy, "S3FlipBuckets")
    assert '"s3:PutObject"' in buckets
    assert "${module.flip_fl_results_bucket.bucket_arn}/*" in buckets
    assert {"kms:Decrypt", "kms:GenerateDataKey"} <= set(re.findall(r'"(kms:\w+)"', policy))


def test_only_the_provisioning_task_runs_under_the_kit_role():
    task_roles = {
        (name, family): role
        for name, source in TASK_DEFINITIONS.items()
        for family, role in re.findall(
            r'resource "aws_ecs_task_definition" "(\w+)".*?task_role_arn\s*=\s*(\S+)', source, re.DOTALL
        )
    }
    assert task_roles[("ecs_efs_provision.tf", "efs_provision")] == PROVISION_ROLE
    assert [family for (_, family), role in task_roles.items() if role == PROVISION_ROLE] == ["efs_provision"]


def test_the_kit_role_reads_only_the_hub_side_of_the_kit():
    read = _statement(_policy("ecs_fl_kit_provision_task"), "S3ReadFlKit")
    paths = set(re.findall(r'aicentre_bucket\.arn\}/([^"]+)"', read))
    assert paths == {
        "fl-flare-participant-kits/*/net-1/services/fl-server-net-1/*",
        "fl-flare-participant-kits/*/net-1/services/flip-fl-api-net-1/*",
        "fl-flower-participant-kits/*/net-1/certificates/*",
        "fl-flower-participant-kits/*/net-1/keys/*.pub",
    }
    assert not re.search(r"s3:(Put|Delete)", _policy("ecs_fl_kit_provision_task"))


def test_the_flower_sync_copies_public_supernode_keys_only():
    assert 'aws s3 sync "$S3_BASE/keys/" /mnt/flower-keys/ --exclude "*" --include "*.pub"' in EFS_PROVISION


def test_no_ecs_role_is_granted_the_ca_state():
    """flip-api reads the kit prefix, where the state sat before the move, so it carries the deny."""
    deny = _statement(_policy("ecs_flip_api_task"), "DenyFlCaState")
    assert re.search(r'effect\s*=\s*"Deny"', deny)
    assert '"s3:*"' in deny
    assert f"${{aws_s3_bucket.aicentre_bucket.arn}}/{CA_STATE_PREFIX}/*" in deny
    assert "${aws_s3_bucket.aicentre_bucket.arn}/fl-flare-participant-kits/*/state/*" in deny

    for name in re.findall(r'data "aws_iam_policy_document" "(\w+)"', IAM_ECS):
        assert not re.search(r'aicentre_bucket\.arn\}/\*?"', _policy(name)), name
        if name != "ecs_flip_api_task":
            assert CA_STATE_PREFIX not in _policy(name), name


def test_flip_api_only_reads_the_kits():
    """Operators write the kits; flip-api lists slots and reads a trust's kit to hand it out."""
    policy = _policy("ecs_flip_api_task")
    read = _statement(policy, "S3ReadFlKits")
    assert set(re.findall(r'"(s3:\w+)"', read)) == {"s3:GetObject", "s3:HeadObject"}
    assert 'actions = ["s3:ListBucket"]' in re.sub(r" +", " ", _statement(policy, "S3ListFlKits"))
    assert "aicentre_bucket" not in _statement(policy, "S3FlipBuckets")


def test_the_flare_upload_keeps_the_ca_state_out_of_the_kit_prefix():
    makefile = (REPO_ROOT / "fl-services" / "nvflare" / "Makefile").read_text()
    target = makefile.split("\nupload-kits-to-s3:\n", 1)[1].split("\n\n", 1)[0]
    kits, state = (line.strip() for line in target.splitlines() if line.strip().startswith("aws s3 sync"))
    assert "$(FL_KIT_S3)" in kits
    assert '--exclude "state/*"' in kits
    assert state.startswith("aws s3 sync ./$(FL_KIT_WORKSPACE)/net-$(NET_NUMBER)/state $(FL_CA_STATE_S3) ")
    assert re.search(r"^FL_KIT_S3 = s3://\$\(AICENTRE_BUCKET_NAME\)/fl-flare-participant-kits/", makefile, re.MULTILINE)
    assert re.search(rf"^FL_CA_STATE_S3 = s3://\$\(AICENTRE_BUCKET_NAME\)/{CA_STATE_PREFIX}/", makefile, re.MULTILINE)
