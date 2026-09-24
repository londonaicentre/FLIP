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
# Plan role — read-only
############################

data "aws_iam_policy_document" "plan_assume_role" {
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
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      values   = local.plan_workflow_refs
    }
  }
}

resource "aws_iam_role" "terraform_plan" {
  name                 = var.plan_role_name
  description          = "Read-only role for FLIP Terraform plans from GitHub Actions (FLIP#962)"
  assume_role_policy   = data.aws_iam_policy_document.plan_assume_role.json
  max_session_duration = 3600
  tags                 = var.tags
}

# ReadOnlyAccess is broad, and that is a real cost worth naming: it lets the plan
# role read every S3 object in the account, including the state file's sensitive
# values. That is not incidental — `terraform plan` cannot run without reading
# state, and state holds AES_KEY_BASE64 and the DB credentials either way. The
# containment is that this role cannot *write* anything, and that plan output
# renders sensitive values as `(sensitive value)`.
resource "aws_iam_role_policy_attachment" "plan_read_only" {
  role       = aws_iam_role.terraform_plan.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/ReadOnlyAccess"
}

# Explicitly deny state writes. ReadOnlyAccess grants no write today, but an
# operator attaching one more policy to this role later should not silently turn
# the plan role into an apply role. An explicit Deny cannot be overridden by a
# later Allow, so this survives that mistake.
#
# Written as verb prefixes over the whole bucket rather than three verbs on two
# exact keys. The narrow form only guarded `flip/terraform.tfstate` and its lock
# — it left this root's own state (`flip/ci/terraform.tfstate`), any future
# workspace, and object-metadata writes such as `s3:PutObjectAcl` and
# `s3:PutObjectTagging` untouched, which is not what "cannot write state" is
# supposed to mean.
data "aws_iam_policy_document" "plan_deny_state_writes" {
  statement {
    effect = "Deny"
    actions = [
      "s3:Abort*",
      "s3:Bypass*",
      "s3:Delete*",
      "s3:ObjectOwnerOverrideToBucketOwner",
      "s3:Put*",
      "s3:Replicate*",
      "s3:Restore*",
    ]
    resources = [
      local.state_arn,
      local.state_all_objects_arn,
    ]
  }
}

resource "aws_iam_role_policy" "plan_deny_state_writes" {
  name   = "deny-terraform-state-writes"
  role   = aws_iam_role.terraform_plan.id
  policy = data.aws_iam_policy_document.plan_deny_state_writes.json
}

# ReadOnlyAccess deliberately withholds secretsmanager:GetSecretValue — AWS
# excludes it precisely because it returns secret material, so its absence is a
# decision in the managed policy rather than an oversight. But `terraform plan`
# refreshes module.flip_api_secret's aws_secretsmanager_secret_version, and
# without this grant every plan dies on AccessDeniedException before producing
# any diff at all.
#
# This is not the widening it looks like. To plan at all the role must read the
# state object, and state already stores this secret's value in clear — the same
# AES_KEY_BASE64 and internal service key. The containment is unchanged: the
# role still cannot write anything, here or to state.
data "aws_iam_policy_document" "plan_read_flip_api_secret" {
  # checkov:skip=CKV_AWS_356:the kms:Decrypt statement below cannot name the key without making this bootstrap depend on the FLIP root — see its comment; the kms:ViaService condition plus the scoped GetSecretValue above is the actual boundary
  statement {
    sid    = "ReadFlipApiSecretForRefresh"
    effect = "Allow"
    actions = [
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetSecretValue",
    ]
    # Secrets Manager appends a random six-character suffix to the ARN, so the
    # name on its own cannot be matched exactly.
    resources = [
      "arn:${data.aws_partition.current.partition}:secretsmanager:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:secret:${var.flip_api_secret_name}-*",
    ]
  }

  # The secret is encrypted with the FLIP application CMK (aws_kms_key.flip_app_key,
  # ../../kms.tf), so GetSecretValue alone still fails with "Access to KMS is not
  # allowed" — ReadOnlyAccess grants no kms:Decrypt.
  #
  # The key is deliberately NOT named here. Resolving it (data.aws_kms_alias
  # "alias/flip-app-key") would make this module fail to apply until the FLIP root
  # exists, and the ordering runs the other way: in a new account — the LZA
  # migration in FLIP#749 — these roles have to exist before CI can apply
  # anything. So it is scoped by condition instead, the same kms:ViaService
  # pattern ../../rds_proxy.tf uses for the RDS master-secret key.
  #
  # The effective boundary is the intersection with the statement above: this
  # role can only decrypt through Secrets Manager in this region, and the only
  # secret it may read is FLIP_API.
  statement {
    sid       = "DecryptFlipApiSecret"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${data.aws_region.current.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "plan_read_flip_api_secret" {
  name   = "flip-terraform-plan-read-secret"
  role   = aws_iam_role.terraform_plan.id
  policy = data.aws_iam_policy_document.plan_read_flip_api_secret.json
}
