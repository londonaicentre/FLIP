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

# GitHub Actions OIDC roles for the FLIP Terraform pipeline (FLIP#962).
#
# Two roles per account:
#
#   AICentre-FLIPTerraformPlanRole   read-only; assumed by PR plans and the
#                                    nightly drift run. Cannot write state.
#   AICentre-FLIPTerraformApplyRole  assumed only by a push-triggered apply on
#                                    the environment's branch.
#
# Modelled on the LZA pattern (lza/cloudformation/{github-oidc,
# terraform-cross-account-role}.yaml) but deliberately NOT joined to it: the LZA
# management role trusts `repo:${org}/${repo}:*` with AdministratorAccess, and
# adding FLIP to that chain would hand org-wide admin to every branch of a public
# repository. These roles are FLIP-scoped and live in the FLIP accounts.
#
# Applied from a laptop, per environment:
#     make -C deploy/providers/AWS/ci apply                 # stag
#     make -C deploy/providers/AWS/ci apply PROD=true       # prod

terraform {
  required_version = ">= 1.13.1"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.AWS_REGION

  default_tags {
    tags = var.tags
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# Looked up, never declared. The provider already exists in both FLIP accounts
# (it backs GitHubAction-AssumeRoleWithAction-FLIP, used by the XNAT image build).
# Declaring it as a `resource` would fail with EntityAlreadyExists on first apply,
# and — worse — a later `terraform destroy` of this root would delete a provider
# other workflows depend on.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

locals {
  repo = "${var.github_org}/${var.github_repo}"

  # The `sub` claim GitHub mints for a job that declares `environment: <name>`.
  #
  # This is the part most easily got wrong: when a job declares an environment,
  # `sub` becomes `repo:ORG/REPO:environment:NAME` — it does NOT carry the ref.
  # Conditioning on `repo:ORG/REPO:ref:refs/heads/main` would therefore never
  # match an environment job, and a trust policy written that way silently fails
  # closed (every apply denied) or, if `:*` is used instead, silently fails open.
  # Q4 forces every job to declare an environment, because that is the only way a
  # workflow can read environment secrets — so every trust policy here is written
  # against the environment form.
  oidc_sub = "repo:${local.repo}:environment:${var.github_environment}"

  # The ref-carrying claim. `job_workflow_ref` names the workflow file *and* the
  # ref it was loaded from, and a pull request cannot forge it: a PR-triggered run
  # reports `@refs/pull/<n>/merge`, never `@refs/heads/<branch>`. Pinning the apply
  # role to `@refs/heads/${var.apply_branch}` is therefore what actually stops a
  # contributor from opening a PR that edits the apply workflow and applies to prod.
  workflow_ref_prefix = "${local.repo}/.github/workflows"

  apply_workflow_ref = "${local.workflow_ref_prefix}/${var.apply_workflow_file}@refs/heads/${var.apply_branch}"

  # Plan runs from PR merge refs and from the drift schedule on the apply branch,
  # so its allowed set is a list of patterns rather than one exact string.
  plan_workflow_refs = [
    "${local.workflow_ref_prefix}/${var.plan_workflow_file}@refs/pull/*/merge",
    "${local.workflow_ref_prefix}/${var.plan_workflow_file}@refs/heads/${var.apply_branch}",
    "${local.workflow_ref_prefix}/${var.drift_workflow_file}@refs/heads/${var.apply_branch}",
  ]

  state_arn        = "arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket_name}"
  state_object_arn = "${local.state_arn}/${var.state_key}"

  # Native S3 state locking (`use_lockfile = true`, backend.tf) writes a sibling
  # `<key>.tflock` object. An apply role that can write the state but not the lock
  # fails at the very end of the run, after changes are already made — so grant
  # both or neither.
  state_lock_arn = "${local.state_arn}/${var.state_key}.tflock"
}

############################
# Plan role — read-only
############################

data "aws_iam_policy_document" "plan_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
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
  name                 = "AICentre-FLIPTerraformPlanRole"
  description          = "Read-only role for FLIP Terraform plans from GitHub Actions (FLIP#962)"
  assume_role_policy   = data.aws_iam_policy_document.plan_assume_role.json
  max_session_duration = 3600
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
data "aws_iam_policy_document" "plan_deny_state_writes" {
  statement {
    effect = "Deny"
    actions = [
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
    ]
    resources = [
      local.state_object_arn,
      local.state_lock_arn,
    ]
  }
}

resource "aws_iam_role_policy" "plan_deny_state_writes" {
  name   = "deny-terraform-state-writes"
  role   = aws_iam_role.terraform_plan.id
  policy = data.aws_iam_policy_document.plan_deny_state_writes.json
}

############################
# Apply role
############################

data "aws_iam_policy_document" "apply_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
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

    # StringEquals, not StringLike: exactly one workflow file at exactly one ref.
    # This is the condition that makes "automatic apply on merge to main"
    # (decision 1) safe to switch on — a PR editing the apply workflow presents
    # `@refs/pull/<n>/merge` and is denied.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      values   = [local.apply_workflow_ref]
    }
  }
}

resource "aws_iam_role" "terraform_apply" {
  name                 = "AICentre-FLIPTerraformApplyRole"
  description          = "Role for FLIP Terraform applies from GitHub Actions on ${var.apply_branch} (FLIP#962)"
  assume_role_policy   = data.aws_iam_policy_document.apply_assume_role.json
  max_session_duration = 3600
}

# PowerUserAccess rather than AdministratorAccess (which the LZA template uses):
# everything the FLIP root manages except IAM, and the IAM it genuinely needs is
# granted explicitly below. The difference that matters is that a power user
# cannot rewrite the account's identity boundary.
resource "aws_iam_role_policy_attachment" "apply_power_user" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/PowerUserAccess"
}

# The FLIP root owns the ECS task and execution roles in iam_ecs.tf, so the apply
# role needs IAM write. Scoped by an explicit Deny on the CI roles themselves,
# which is the guardrail that keeps this from being AdministratorAccess by
# another name: an apply cannot widen its own permissions or relax its own trust
# policy. Changing these roles stays a laptop operation against this root.
data "aws_iam_policy_document" "apply_iam" {
  statement {
    sid    = "ManageServiceRoles"
    effect = "Allow"
    actions = [
      "iam:AddRoleToInstanceProfile",
      "iam:AttachRolePolicy",
      "iam:CreateInstanceProfile",
      "iam:CreateOpenIDConnectProvider",
      "iam:CreatePolicy",
      "iam:CreatePolicyVersion",
      "iam:CreateRole",
      "iam:CreateServiceLinkedRole",
      "iam:DeleteInstanceProfile",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PassRole",
      "iam:PutRolePolicy",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:TagPolicy",
      "iam:TagRole",
      "iam:UntagInstanceProfile",
      "iam:UntagPolicy",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "NoSelfEscalation"
    effect = "Deny"
    actions = [
      "iam:AttachRolePolicy",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PutRolePolicy",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
    ]
    resources = [
      aws_iam_role.terraform_apply.arn,
      aws_iam_role.terraform_plan.arn,
    ]
  }
}

resource "aws_iam_role_policy" "apply_iam" {
  name   = "flip-terraform-apply-iam"
  role   = aws_iam_role.terraform_apply.id
  policy = data.aws_iam_policy_document.apply_iam.json
}

# State access. PowerUserAccess already covers S3, so this is documentation as
# much as grant — it records exactly which objects the pipeline writes, and the
# lock object is spelled out because a role that can write state but not the
# lock fails after the changes are made, not before.
data "aws_iam_policy_document" "apply_state" {
  statement {
    sid       = "ListStateBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [local.state_arn]
  }

  statement {
    sid    = "ReadWriteState"
    effect = "Allow"
    actions = [
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = [
      local.state_object_arn,
      local.state_lock_arn,
    ]
  }
}

resource "aws_iam_role_policy" "apply_state" {
  name   = "flip-terraform-apply-state"
  role   = aws_iam_role.terraform_apply.id
  policy = data.aws_iam_policy_document.apply_state.json
}
