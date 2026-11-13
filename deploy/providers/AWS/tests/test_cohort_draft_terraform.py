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
"""Cohort SQL drafting (FLIP#1556) is opt-in in Terraform, and its IAM grant is scoped.

With ``cohort_draft_enabled`` at its default (false) nothing may be created and the flip-api task env must not
change, so the plan of an estate that has not opted in is empty. Enabled, the task role may call only the
configured model and apply only the drafting guardrail: no ``*`` resource.
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

AWS_DIR = Path(__file__).resolve().parents[1]
DRAFT = strip_comments((AWS_DIR / "bedrock_cohort_draft.tf").read_text())
VARIABLES = strip_comments((AWS_DIR / "variables.tf").read_text())
LOCALS = strip_comments((AWS_DIR / "locals.tf").read_text())
ENABLED_COUNT = "count = var.cohort_draft_enabled ? 1 : 0"


def _blocks() -> list[str]:
    return re.findall(r'^(?:resource|data)\s+"\w+"\s+"\w+"', DRAFT, re.M)


def _statements(policy: str) -> list[str]:
    bodies, rest = [], policy
    while (match := re.search(r"^\s*(?:dynamic \"statement\"|statement)\s*\{", rest, re.M)) is not None:
        body = hcl_block(rest, match.group(0).strip())
        bodies.append(body)
        rest = rest[match.end() + len(body) :]
    return bodies


def test_the_feature_is_off_by_default():
    body = hcl_block(VARIABLES, 'variable "cohort_draft_enabled"')
    assert re.search(r"type\s*=\s*bool", body)
    assert re.search(r"default\s*=\s*false", body)


def test_every_block_is_created_only_when_enabled():
    blocks = _blocks()
    assert len(blocks) == 4, blocks  # the guardrail, its version, the policy document and the role policy
    for header in blocks:
        assert ENABLED_COUNT in re.sub(r"\s+", " ", hcl_block(DRAFT, header)), f"{header} is not gated"


def test_the_task_env_is_unchanged_while_disabled():
    env = re.search(r"cohort_draft_env\s*=\s*var\.cohort_draft_enabled\s*\?\s*\{(.*?)\}\s*:\s*\{\}", DRAFT, re.S)
    assert env, "cohort_draft_env must be the empty map while disabled"
    for key in ("COHORT_DRAFT_ENABLED", "COHORT_DRAFT_GUARDRAIL_ID", "COHORT_DRAFT_GUARDRAIL_VERSION"):
        assert key in env.group(1)
    assert re.search(r"flip_api\s*=\s*merge\([^{]*local\.cohort_draft_env", LOCALS)


def test_the_grant_names_no_wildcard_resource():
    policy = hcl_block(DRAFT, 'data "aws_iam_policy_document" "cohort_draft"')
    statements = _statements(policy)
    assert len(statements) == 4
    for statement in statements:
        resources = re.search(r"resources\s*=\s*\[(.*?)\]", statement, re.S)
        assert resources, statement
        assert not re.search(r'"\*"', resources.group(1)), statement
        assert "effect" not in statement or 'effect = "Allow"' in statement
    assert '"arn:aws:bedrock:${var.AWS_REGION}::foundation-model/${local.cohort_draft_foundation_model}"' in policy
    assert "aws_bedrock_guardrail.cohort_draft[0].guardrail_arn" in policy


def test_a_profile_grant_is_bound_to_the_profile():
    """Foundation-model ARNs in other Regions are reachable only through the configured inference profile."""
    policy = hcl_block(DRAFT, 'data "aws_iam_policy_document" "cohort_draft"')
    via_profile = next(s for s in _statements(policy) if "BedrockInvokeCohortDraftModelViaProfile" in s)
    assert "for_each = local.cohort_draft_model_is_profile ? [1] : []" in via_profile
    assert 'variable = "bedrock:InferenceProfileArn"' in via_profile
    assert "values   = [local.cohort_draft_profile_arn]" in via_profile
    mantle = next(s for s in _statements(policy) if "bedrock-mantle:CreateInference" in s)
    assert 'variable = "bedrock-mantle:Model"' in mantle


def test_no_account_id_is_written_down():
    assert not re.search(r"\b\d{12}\b", (AWS_DIR / "bedrock_cohort_draft.tf").read_text())
