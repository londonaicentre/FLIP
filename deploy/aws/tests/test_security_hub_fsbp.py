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

"""Static guards for the Security Hub FSBP controls the hub's resources pass (FLIP#1796).

Each test names the control it pins. checkov cannot hold these: ``checkov_lint.sh`` runs with
``--skip-download``, so resources built inside registry modules (the RDS instance, the NLB) are
never scanned.
"""

import re
from pathlib import Path

import pytest
from tf_source import hcl_block, strip_comments

ROOT = Path(__file__).resolve().parent.parent


def _block(file: str, header: str) -> str:
    return strip_comments(hcl_block((ROOT / file).read_text(), header))


def _sets(body: str, argument: str, value: str) -> bool:
    return re.search(rf"^\s*{argument}\s*=\s*{re.escape(value)}\s*$", body, re.MULTILINE) is not None


@pytest.mark.parametrize(
    ("control", "argument", "value"),
    [
        ("RDS.8", "deletion_protection", "true"),
        ("RDS.17", "copy_tags_to_snapshot", "true"),
        ("RDS.10", "iam_database_authentication_enabled", "true"),
        ("RDS.9/RDS.36", "enabled_cloudwatch_logs_exports", '["postgresql", "upgrade"]'),
        ("RDS.9/RDS.36", "create_cloudwatch_log_group", "true"),
        ("RDS.6", "monitoring_interval", "60"),
        ("RDS.6", "create_monitoring_role", "true"),
        ("RDS.6", "monitoring_role_permissions_boundary", "local.iam_permissions_boundary_arn"),
    ],
)
def test_flip_db_meets_control(control: str, argument: str, value: str) -> None:
    """The database module call sets each control's argument to its literal value."""
    body = _block("main.tf", 'module "flip_db"')
    assert _sets(body, argument, value), f'module "flip_db" must set {argument} = {value} ({control})'


def test_rds_monitoring_role_is_one_the_ci_apply_role_may_create() -> None:
    """RDS.6: the role name must be in the bootstrap's managed_role_names, or every CI apply fails."""
    body = _block("main.tf", 'module "flip_db"')
    name = re.search(r'^\s*monitoring_role_name\s*=\s*"([^"]+)"', body, re.MULTILINE)
    assert name, 'module "flip_db" must name its monitoring role'
    managed = _block("modules/terraform_ci_bootstrap/variables.tf", 'variable "managed_role_names"')
    assert f'"{name.group(1)}"' in managed, f"add {name.group(1)} to the bootstrap's managed_role_names"


def _aborts_multipart_uploads(body: str) -> bool:
    return re.search(r"abort_incomplete_multipart_upload\s*\{\s*days_after_initiation\s*=\s*\d+", body) is not None


@pytest.mark.parametrize(
    ("file", "header"),
    [
        ("modules/flip_s3_bucket/main.tf", 'resource "aws_s3_bucket_lifecycle_configuration" "this"'),
        ("services.tf", 'resource "aws_s3_bucket_lifecycle_configuration" "aicentre_bucket"'),
        ("cloudfront.tf", 'resource "aws_s3_bucket_lifecycle_configuration" "flip_ui"'),
    ],
)
def test_bucket_aborts_incomplete_multipart_uploads(file: str, header: str) -> None:
    """S3.13: every application bucket has a lifecycle configuration with the abort rule."""
    body = _block(file, header)
    assert _aborts_multipart_uploads(body), f"{header} in {file} must abort incomplete multipart uploads (S3.13)"


def test_module_lifecycle_is_unconditional() -> None:
    """S3.13: the module's lifecycle configuration exists for every bucket, not only the opted-in ones."""
    body = _block("modules/flip_s3_bucket/main.tf", 'resource "aws_s3_bucket_lifecycle_configuration" "this"')
    assert not re.search(r"^ {2}(count|for_each)\s*=", body, re.MULTILINE), (
        "the bucket module's lifecycle configuration must not be conditional (S3.13)"
    )


def test_fl_internal_nlb_has_deletion_protection() -> None:
    """ELB.6: the internal NLB, whose static IPs the networking account registers, cannot be deleted."""
    body = _block("fl_ingress_lza.tf", 'module "fl_server_internal_nlb"')
    assert _sets(body, "enable_deletion_protection", "true"), "flip-fl-internal-nlb needs deletion protection (ELB.6)"
