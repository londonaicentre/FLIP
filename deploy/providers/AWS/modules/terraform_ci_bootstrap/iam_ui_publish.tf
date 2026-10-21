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
# UI-publish role (FLIP#1186)
############################

# Publishing flip-ui needs write access to one bucket, so it does not run under
# the apply role, which holds s3:* on every bucket in the account. Created only
# when the caller names the UI bucket.
#
# The trust pins the reusable workflow, not its callers. For a job inside a
# reusable workflow GitHub sets job_workflow_ref to the *called* file, so
# terraform_apply.yml's `ui` job presents `publish_ui.yml@refs/heads/<branch>`,
# and a pull request presents `@refs/pull/<n>/merge` and is denied.
data "aws_iam_policy_document" "ui_publish_assume_role" {
  count = local.ui_publish ? 1 : 0

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
      values   = [local.oidc_sub]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      values   = [local.ui_publish_workflow_ref]
    }
  }
}

resource "aws_iam_role" "ui_publish" {
  count = local.ui_publish ? 1 : 0

  name                 = var.ui_publish_role_name
  description          = "Role that publishes the flip-ui build to its S3 bucket from GitHub Actions on ${var.apply_branch} (FLIP#1186)"
  assume_role_policy   = data.aws_iam_policy_document.ui_publish_assume_role[0].json
  max_session_duration = 3600
  tags                 = var.tags
}

# The bucket is SSE-S3 (cloudfront.tf in the FLIP root), so no KMS grant.
data "aws_iam_policy_document" "ui_publish" {
  count = local.ui_publish ? 1 : 0

  statement {
    sid       = "ListTheUiBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [local.ui_bucket_arn]
  }

  statement {
    sid    = "SyncTheUiObjects"
    effect = "Allow"
    actions = [
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${local.ui_bucket_arn}/*"]
  }

  # The Ark+ demo shares the bucket under ark_demo/ and is published by hand
  # (make deploy-ark-demo). The sync already excludes it; this makes a broken
  # sync unable to delete it either.
  statement {
    sid    = "NeverTouchTheArkDemo"
    effect = "Deny"
    actions = [
      "s3:DeleteObject",
      "s3:PutObject",
    ]
    resources = ["${local.ui_bucket_arn}/ark_demo/*"]
  }

  # Only where the FLIP root owns the distribution. On LZA the networking
  # account's edge serves the bucket and nothing is invalidated (FLIP#749).
  dynamic "statement" {
    for_each = var.ui_distribution_arn == "" ? [] : [var.ui_distribution_arn]

    content {
      sid       = "InvalidateTheUiDistribution"
      effect    = "Allow"
      actions   = ["cloudfront:CreateInvalidation"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "ui_publish" {
  count = local.ui_publish ? 1 : 0

  name   = "flip-ui-publish"
  role   = aws_iam_role.ui_publish[0].id
  policy = data.aws_iam_policy_document.ui_publish[0].json
}
