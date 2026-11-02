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

############################
# Snowflake trust state roles (epic #1589)
############################

# The Snowflake trust's two Terraform roots keep their (OpenTofu-encrypted) state in this bucket,
# under var.snowflake_state_prefix. Their workflows assume one of these roles for the state and
# nothing else: Snowflake itself is reached through Snowflake workload identity federation, so
# neither role carries any power over Snowflake, and neither holds any other AWS permission. The
# hub's plan and apply roles stay exactly as they are; widening them would hand the Snowflake
# workflows the whole hub apply policy.
#
# Created only when snowflake_github_environment is named.

data "aws_iam_policy_document" "snowflake_state_plan_assume_role" {
  count = local.snowflake_state ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.snowflake_plan_oidc_sub]
    }

    # StringLike: the pull-request ref carries the PR number.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      values   = local.snowflake_plan_workflow_refs
    }
  }
}

data "aws_iam_policy_document" "snowflake_state_apply_assume_role" {
  count = local.snowflake_state ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.snowflake_oidc_sub]
    }

    # StringEquals at the branch ref: a pull request presents @refs/pull/<n>/merge and is denied.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      values   = local.snowflake_apply_workflow_refs
    }
  }
}

resource "aws_iam_role" "snowflake_state_plan" {
  count = local.snowflake_state ? 1 : 0

  name                 = var.snowflake_state_plan_role_name
  description          = "Reads the Snowflake trust's Terraform state for PR plans and drift (epic #1589); no other permission"
  assume_role_policy   = data.aws_iam_policy_document.snowflake_state_plan_assume_role[0].json
  max_session_duration = 3600
  tags                 = var.tags
}

resource "aws_iam_role" "snowflake_state_apply" {
  count = local.snowflake_state ? 1 : 0

  name                 = var.snowflake_state_apply_role_name
  description          = "Reads and writes the Snowflake trust's Terraform state on ${var.apply_branch} (epic #1589); no other permission"
  assume_role_policy   = data.aws_iam_policy_document.snowflake_state_apply_assume_role[0].json
  max_session_duration = 3600
  tags                 = var.tags
}

# Read only. Plans and drift run with -lock=false, as the hub's do, so the lock is never written.
data "aws_iam_policy_document" "snowflake_state_plan" {
  count = local.snowflake_state ? 1 : 0

  statement {
    sid       = "ListOnlyTheSnowflakePrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [local.state_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.snowflake_state_prefix}/*"]
    }
  }

  statement {
    sid       = "ReadTheSnowflakeState"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = [local.snowflake_state_objects_arn]
  }
}

# The state objects and their .tflock siblings (S3 native locking) under the prefix, nothing more.
data "aws_iam_policy_document" "snowflake_state_apply" {
  count = local.snowflake_state ? 1 : 0

  statement {
    sid       = "ListOnlyTheSnowflakePrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [local.state_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.snowflake_state_prefix}/*"]
    }
  }

  statement {
    sid    = "ReadWriteTheSnowflakeState"
    effect = "Allow"
    actions = [
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = [local.snowflake_state_objects_arn]
  }
}

resource "aws_iam_role_policy" "snowflake_state_plan" {
  count = local.snowflake_state ? 1 : 0

  name   = "flip-snowflake-state-read"
  role   = aws_iam_role.snowflake_state_plan[0].id
  policy = data.aws_iam_policy_document.snowflake_state_plan[0].json
}

resource "aws_iam_role_policy" "snowflake_state_apply" {
  count = local.snowflake_state ? 1 : 0

  name   = "flip-snowflake-state-read-write"
  role   = aws_iam_role.snowflake_state_apply[0].id
  policy = data.aws_iam_policy_document.snowflake_state_apply[0].json
}
