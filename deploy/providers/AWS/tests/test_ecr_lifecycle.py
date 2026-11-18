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

"""Static guard: every ECR pull-through cache repository carries a lifecycle policy (FLIP#1796, ECR.3).

The cache rules are created out of band and ECR creates their repositories on first pull, so
``ecr.tf`` covers them twice: a repository creation template per cache prefix for the repositories
created from now on, and ``aws_ecr_lifecycle_policy`` over the repositories that already exist. Both
must follow the prefixes the pull-through IAM policy in ``iam_ecs.tf`` grants, or a new cache rule
lands with repositories Security Hub fails again.
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

AWS_ROOT = Path(__file__).resolve().parent.parent
ECR_TF = AWS_ROOT / "ecr.tf"
IAM_ECS_TF = AWS_ROOT / "iam_ecs.tf"

POLICY_LOCAL = "local.ecr_pull_through_lifecycle_policy"


def _ecr_source() -> str:
    return strip_comments(ECR_TF.read_text())


def _declared_prefixes() -> set[str]:
    match = re.search(r"ecr_pull_through_prefixes\s*=\s*\[([^\]]*)\]", hcl_block(_ecr_source(), "locals"))
    assert match, "ecr.tf must declare local.ecr_pull_through_prefixes as a list literal"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _iam_cache_prefixes() -> set[str]:
    body = hcl_block(
        strip_comments(IAM_ECS_TF.read_text()), 'data "aws_iam_policy_document" "ecs_task_execution_ecr_pull_through"'
    )
    return set(re.findall(r':repository/([^/"]+)/\*"', body))


def test_prefixes_match_the_pull_through_iam_policy() -> None:
    """The prefixes ecr.tf covers are exactly the cache prefixes the execution role may import into."""
    iam_prefixes = _iam_cache_prefixes()
    assert iam_prefixes, "found no repository/<prefix>/* ARNs in iam_ecs.tf — the regex has drifted from the HCL"
    assert _declared_prefixes() == iam_prefixes


def test_creation_templates_carry_the_policy_for_pull_through_caches() -> None:
    """One template per prefix, applied to pull-through cache repositories, with the shared policy."""
    body = hcl_block(_ecr_source(), 'resource "aws_ecr_repository_creation_template" "pull_through"')
    assert re.search(r"^\s*for_each\s*=\s*toset\(local\.ecr_pull_through_prefixes\)\s*$", body, re.MULTILINE)
    assert re.search(r"^\s*prefix\s*=\s*each\.key\s*$", body, re.MULTILINE)
    assert re.search(r'^\s*applied_for\s*=\s*\["PULL_THROUGH_CACHE"\]\s*$', body, re.MULTILINE)
    assert re.search(rf"^\s*lifecycle_policy\s*=\s*{re.escape(POLICY_LOCAL)}\s*$", body, re.MULTILINE)


def test_existing_repositories_under_every_prefix_get_the_policy() -> None:
    """Templates only reach new repositories; the existing ones under each prefix get the policy directly."""
    source = _ecr_source()
    assert 'data "aws_ecr_repositories" "all"' in source
    body = hcl_block(source, 'resource "aws_ecr_lifecycle_policy" "pull_through"')
    assert re.search(
        r"for name in data\.aws_ecr_repositories\.all\.names : name\s+"
        r"if anytrue\(\[for prefix in local\.ecr_pull_through_prefixes : startswith\(name, \"\$\{prefix\}/\"\)\]\)",
        body,
    ), "the existing-repository filter must keep every repository under a prefix in local.ecr_pull_through_prefixes"
    assert re.search(rf"^\s*policy\s*=\s*{re.escape(POLICY_LOCAL)}\s*$", body, re.MULTILINE)


def test_policy_has_rules_that_only_expire_tagged_images() -> None:
    """The shared policy is a jsonencode() object with at least one rule, none of them selecting untagged images.

    Untagged images in a cache repository are the platform manifests of a tagged index, or digests a running
    ECS deployment still pulls by; expiring them can break a tagged image.
    """
    policy = hcl_block(hcl_block(_ecr_source(), "locals"), "ecr_pull_through_lifecycle_policy = jsonencode(")
    assert re.search(r"^\s*rules\s*=\s*\[", policy, re.MULTILINE), "the lifecycle policy must have a rules list"
    assert re.findall(r"^\s*rulePriority\s*=\s*\d+\s*$", policy, re.MULTILINE), "the policy needs at least one rule"
    assert set(re.findall(r'tagStatus\s*=\s*"([^"]+)"', policy)) == {"tagged"}
    assert re.search(r'action\s*=\s*\{\s*type\s*=\s*"expire"\s*\}', policy)
