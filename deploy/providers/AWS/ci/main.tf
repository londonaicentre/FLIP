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

  # Plan runs from two places, so its allowed set is a list of patterns rather
  # than one exact string.
  #
  #   * PR plans, only ever against staging. terraform_plan.yml declares
  #     `environment: aws-stag` unconditionally, so the production plan role is
  #     never reachable from a pull request — listing `refs/pull/*/merge` there
  #     would be dead weight that reads like a permission.
  #   * The nightly drift run, from `drift_branch`. That is the branch the
  #     *workflow file* is loaded from, which for a schedule is always the
  #     repository default branch — NOT `apply_branch`. Deriving it from
  #     `apply_branch` is what left the production drift job unable to assume
  #     anything: it presents `@refs/heads/develop` and the policy trusted
  #     `@refs/heads/main` only. See the drift_branch variable.
  #
  # `terraform_plan.yml@refs/heads/<branch>` is deliberately absent: that
  # workflow has no push trigger, so no run can ever present it.
  plan_workflow_refs = concat(
    var.environment == "stag" ? ["${local.workflow_ref_prefix}/${var.plan_workflow_file}@refs/pull/*/merge"] : [],
    ["${local.workflow_ref_prefix}/${var.drift_workflow_file}@refs/heads/${var.drift_branch}"],
  )

  state_arn        = "arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket_name}"
  state_object_arn = "${local.state_arn}/${var.state_key}"

  # Native S3 state locking (`use_lockfile = true`, backend.tf) writes a sibling
  # `<key>.tflock` object. An apply role that can write the state but not the lock
  # fails at the very end of the run, after changes are already made — so grant
  # both or neither.
  state_lock_arn = "${local.state_arn}/${var.state_key}.tflock"

  # Every object in the state bucket, for the plan role's Deny. Naming the two
  # exact keys was too narrow to be a guardrail: a second root's state (this one
  # lives at flip/ci/terraform.tfstate), a `-state-out` file or a new workspace
  # would all fall outside it.
  state_all_objects_arn = "${local.state_arn}/*"

  managed_role_arns = [
    for name in var.managed_role_names :
    "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${name}"
  ]

  attachable_policy_arns = [
    for name in var.attachable_managed_policies :
    "arn:${data.aws_partition.current.partition}:iam::aws:policy/${name}"
  ]
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
  # checkov:skip=CKV_AWS_356:the kms:Decrypt statement below cannot name the key without making this bootstrap root depend on the FLIP root — see its comment; the kms:ViaService condition plus the scoped GetSecretValue above is the actual boundary
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
      "arn:${data.aws_partition.current.partition}:secretsmanager:${var.AWS_REGION}:${data.aws_caller_identity.current.account_id}:secret:${var.flip_api_secret_name}-*",
    ]
  }

  # The secret is encrypted with the FLIP application CMK (aws_kms_key.flip_app_key,
  # ../kms.tf), so GetSecretValue alone still fails with "Access to KMS is not
  # allowed" — ReadOnlyAccess grants no kms:Decrypt.
  #
  # The key is deliberately NOT named here. Resolving it (data.aws_kms_alias
  # "alias/flip-app-key") would make this root fail to apply until the FLIP root
  # exists, and the ordering runs the other way: in a new account — the LZA
  # migration in FLIP#749 — these roles have to exist before CI can apply
  # anything. So it is scoped by condition instead, the same kms:ViaService
  # pattern ../rds_proxy.tf uses for the RDS master-secret key.
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
      values   = ["secretsmanager.${var.AWS_REGION}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "plan_read_flip_api_secret" {
  name   = "flip-terraform-plan-read-secret"
  role   = aws_iam_role.terraform_plan.id
  policy = data.aws_iam_policy_document.plan_read_flip_api_secret.json
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

############################
# The permissions boundary
############################
#
# Declared here, in the root the pipeline does NOT apply, and set by the FLIP
# root on every role it owns (`iam_permissions_boundary_name`, ../variables.tf).
# The apply role may only create or grant to a role that carries it, which is
# what bounds a role the pipeline mints to no more than the pipeline itself has.
#
# It is deliberately generous: allow everything, then deny identity management.
# That is PowerUserAccess's own shape, and PowerUserAccess is what the apply role
# holds — so a role created by an apply can be at most as powerful as the apply
# that created it, and no FLIP workload loses a runtime permission (none of the
# roles it applies to make an IAM, Organizations or Account call).
data "aws_iam_policy_document" "apply_boundary" {
  # Every broad-policy check fires on this document, and all of them are reading
  # it as a grant. It is not one. A permissions boundary is evaluated as an
  # *intersection*: a role can do only what its identity policy AND its boundary
  # both allow, and the boundary confers nothing by itself. That is also why the
  # Allow cannot be narrowed — a boundary made only of Denies permits nothing at
  # all, and every action left out of the Allow is silently removed from every
  # role that carries this boundary, including the ECS task roles that need S3,
  # Secrets Manager, KMS and CloudWatch at runtime.
  #
  # The security property is in the Deny below (no identity management, no
  # Organizations, no Account) plus the fact that the roles this bounds hold
  # PowerUserAccess at most. Narrowing the Allow to satisfy a linter would break
  # the platform without changing the boundary's effect.
  # checkov:skip=CKV_AWS_1:a permissions boundary is a ceiling, not a grant; Allow "*" is what makes it a ceiling rather than a deny-list that permits nothing
  # checkov:skip=CKV_AWS_49:same — the wildcard action set is the intersection ceiling, and every role carrying it is separately capped by its own identity policy
  # checkov:skip=CKV_AWS_107:credential-exposure verbs are reachable only if a bounded role's own identity policy grants them; the boundary cannot add a permission
  # checkov:skip=CKV_AWS_108:data-exfiltration verbs likewise — the FLIP task roles' own policies are the grant, and they are scoped in ../iam_ecs.tf
  # checkov:skip=CKV_AWS_109:permissions management is exactly what the Deny below removes from every bounded role; the Allow cannot restore it
  # checkov:skip=CKV_AWS_110:privilege escalation is what this boundary exists to prevent — iam:*, account:* and organizations:* are denied outright
  # checkov:skip=CKV_AWS_111:a boundary confers nothing on its own, and narrowing the Allow would strip runtime permissions from every ECS task role that carries it
  # checkov:skip=CKV_AWS_356:the ceiling has to cover every resource the bounded roles legitimately touch; the containment is the Deny below
  # checkov:skip=CKV2_AWS_40:full IAM privileges are denied by the NoIdentityManagement statement, which an intersection cannot be widened past
  statement {
    sid       = "CeilingIsEverythingElse"
    effect    = "Allow"
    actions   = ["*"]
    resources = ["*"]
  }

  # The whole point of the boundary. iam:CreateServiceLinkedRole is left out of
  # the Deny because AWS services create their own linked roles through the
  # calling principal, and denying it breaks that without closing anything.
  statement {
    sid    = "NoIdentityManagement"
    effect = "Deny"
    actions = [
      "iam:Add*",
      "iam:Attach*",
      "iam:Change*",
      "iam:CreateAccessKey",
      "iam:CreateAccountAlias",
      "iam:CreateGroup",
      "iam:CreateInstanceProfile",
      "iam:CreateLoginProfile",
      "iam:CreateOpenIDConnectProvider",
      "iam:CreatePolicy",
      "iam:CreatePolicyVersion",
      "iam:CreateRole",
      "iam:CreateSAMLProvider",
      "iam:CreateUser",
      "iam:CreateVirtualMFADevice",
      "iam:Delete*",
      "iam:Detach*",
      "iam:PassRole",
      "iam:Put*",
      "iam:Remove*",
      "iam:Set*",
      "iam:Tag*",
      "iam:Untag*",
      "iam:Update*",
      "iam:Upload*",
      "account:*",
      "organizations:*",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "apply_boundary" {
  name        = var.permissions_boundary_name
  description = "Permissions boundary for roles the FLIP Terraform pipeline creates (FLIP#962)"
  policy      = data.aws_iam_policy_document.apply_boundary.json
}

# The FLIP root owns the ECS task and execution roles in iam_ecs.tf, so the apply
# role needs IAM write. What keeps that from being AdministratorAccess by another
# name is not one Deny but four separate limits:
#
#   * a role can only be created, or given an inline policy, if it carries the
#     permissions boundary above — so a minted role is capped at what the apply
#     role itself holds, and cannot be given IAM write;
#   * only three named AWS-managed policies may be attached to anything, so
#     `AttachRolePolicy AdministratorAccess` is denied outright;
#   * iam:PassRole and iam:UpdateAssumeRolePolicy — the two verbs that turn a
#     role into a usable identity for someone else — are scoped to the eight
#     roles the FLIP root owns, all of which have literal names;
#   * an explicit Deny on both CI roles and on the boundary policy, so an apply
#     cannot re-trust itself or raise its own ceiling.
#
# What this still does not prevent, stated plainly rather than claimed away: an
# apply can create a role that trusts an external principal and hand it
# everything under the boundary — roughly PowerUser. It cannot exceed itself, but
# it can lend itself out. The control for that is the same one that authorises
# the apply at all: review on the environment's branch, and the trust policy
# pinning job_workflow_ref to terraform_apply.yml at that branch.
data "aws_iam_policy_document" "apply_iam" {
  # checkov:skip=CKV_AWS_109:IAM write is the point of this document — the FLIP root owns the ECS task/execution, RDS proxy, Lambda and EC2 roles, so an apply cannot run without it; the containment is the boundary condition plus the two Denies, not a narrower Allow
  # checkov:skip=CKV_AWS_110:the role-mutation verbs are escalation primitives by nature; every one of them is either gated on iam:PermissionsBoundary or scoped to the eight literally-named roles in var.managed_role_names
  # PowerUserAccess withholds *all* of iam: except CreateServiceLinkedRole,
  # DeleteServiceLinkedRole and ListRoles — the read verbs included. Terraform
  # refreshes every aws_iam_role, aws_iam_role_policy, role-policy attachment and
  # instance profile in the FLIP root on each run, so without these an apply dies
  # during refresh, before it has a plan to gate. Read-only, and no wider than
  # what the plan role already holds through ReadOnlyAccess.
  statement {
    sid    = "ReadIamToRefresh"
    effect = "Allow"
    actions = [
      "iam:Get*",
      "iam:List*",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "CreateAndGrantOnlyInsideTheBoundary"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:PutRolePolicy",
    ]
    # The resource cannot be enumerated: iam:CreateRole is evaluated against the
    # role being created, which by definition does not exist yet. The boundary
    # condition is the bound instead, and it is a tighter one than an ARN list
    # would be — it constrains what the new role can *do*, not just its name.
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.apply_boundary.arn]
    }
  }

  # Attaching a managed policy is the shortest path from "can create a role" to
  # "can create an administrator", so it carries both conditions: the target must
  # be inside the boundary, and the policy must be one of the three the FLIP root
  # actually attaches.
  statement {
    sid       = "AttachOnlyTheManagedPoliciesThisRootUses"
    effect    = "Allow"
    actions   = ["iam:AttachRolePolicy"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.apply_boundary.arn]
    }

    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values   = local.attachable_policy_arns
    }
  }

  # Needed to put the boundary onto a role that predates it (the first apply
  # after FLIP#962) and to restore it if someone strips it by hand. The condition
  # means this can only ever set *our* boundary, never a weaker one.
  statement {
    sid       = "SetTheBoundaryItself"
    effect    = "Allow"
    actions   = ["iam:PutRolePermissionsBoundary"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.apply_boundary.arn]
    }
  }

  # Verbs that can only remove permissions or edit metadata. Left on "*" because
  # iam:TagRole is required by CreateRole when the provider's default_tags apply,
  # and a role being created has no ARN to enumerate.
  statement {
    sid    = "MaintainRoles"
    effect = "Allow"
    actions = [
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
    ]
    resources = ["*"]
  }

  # The two verbs that make a role usable by something else. Scoped, because
  # every role this pipeline manages has a literal name.
  statement {
    sid    = "PassAndRetrustOnlyTheKnownRoles"
    effect = "Allow"
    actions = [
      "iam:PassRole",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = local.managed_role_arns
  }

  statement {
    sid    = "InstanceProfiles"
    effect = "Allow"
    actions = [
      "iam:AddRoleToInstanceProfile",
      "iam:CreateInstanceProfile",
      "iam:DeleteInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:UntagInstanceProfile",
    ]
    # An instance profile is only reachable by an EC2 instance the apply also
    # launches, and launching one requires iam:PassRole on the role inside it —
    # which is scoped above.
    resources = ["*"]
  }

  statement {
    sid       = "ServiceLinkedRoles"
    effect    = "Allow"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]
  }

  statement {
    sid    = "NoSelfEscalation"
    effect = "Deny"
    actions = [
      "iam:AttachRolePolicy",
      "iam:DeleteRole",
      "iam:DeleteRolePermissionsBoundary",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PassRole",
      "iam:PutRolePermissionsBoundary",
      "iam:PutRolePolicy",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
    ]
    resources = [
      aws_iam_role.terraform_apply.arn,
      aws_iam_role.terraform_plan.arn,
    ]
  }

  # A boundary an apply can rewrite is not a boundary. PowerUserAccess withholds
  # every IAM write, and none of the Allows above name a policy resource, so this
  # is belt and braces — but it is the one object whose integrity the rest of
  # this document depends on.
  statement {
    sid    = "NoBoundaryTampering"
    effect = "Deny"
    actions = [
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
    ]
    resources = [aws_iam_policy.apply_boundary.arn]
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
