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
# Apply role
############################

data "aws_iam_policy_document" "apply_assume_role" {
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
  name                 = var.apply_role_name
  description          = "Role for FLIP Terraform applies from GitHub Actions on ${var.apply_branch} (FLIP#962)"
  assume_role_policy   = data.aws_iam_policy_document.apply_assume_role.json
  max_session_duration = 3600
  tags                 = var.tags
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
