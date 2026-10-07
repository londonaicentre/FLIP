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

# The AWS services the FLIP root uses, and nothing else: an allowlist in place of
# a managed policy such as PowerUserAccess, which would grant every non-IAM
# service in the account (secrets the pipeline has no business reading, services
# FLIP never touches, any region the account allows). Limited to the account's
# region plus us-east-1 (local.apply_regions). A resource-level scope inside each
# service would mean tracking every FLIP resource name here as well; the service
# and region bound is the one kept exact, by a test that derives the list from
# the FLIP root's resource types.
data "aws_iam_policy_document" "apply_services" {
  # checkov:skip=CKV_AWS_356:the FLIP root creates resources whose ARNs do not exist until it runs; the bound is the service allowlist and the region condition
  # checkov:skip=CKV_AWS_111:writes are the purpose of an apply role; they are limited to the services the FLIP root declares resources in (var.apply_service_prefixes), in its regions
  # checkov:skip=CKV_AWS_108:the apply refreshes and writes the FLIP root's secrets, parameters and objects; no Allow outside its services
  # checkov:skip=CKV_AWS_109:no IAM service in the allowlist (a variable validation refuses it); resource policies on S3, KMS, SNS, SQS and Secrets Manager are FLIP root resources
  # checkov:skip=CKV_AWS_107:credential exposure is bounded by the service allowlist; IAM, STS and Organizations are refused by a variable validation
  # checkov:skip=CKV_AWS_110:no IAM action is granted here; the IAM grant is apply_iam, scoped to the named roles
  # checkov:skip=CKV2_AWS_40:no IAM action is granted here; the IAM grant is apply_iam, scoped to the named roles
  statement {
    sid       = "TheServicesTheFlipRootUses"
    effect    = "Allow"
    actions   = [for prefix in var.apply_service_prefixes : "${prefix}:*"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = local.apply_regions
    }
  }
}

resource "aws_iam_role_policy" "apply_services" {
  name   = "flip-terraform-apply-services"
  role   = aws_iam_role.terraform_apply.id
  policy = data.aws_iam_policy_document.apply_services.json
}

# The FLIP root owns the ECS task and execution roles in iam_ecs.tf, so the apply
# role needs IAM write. What keeps that from being AdministratorAccess by another
# name is a set of separate limits:
#
#   * every role-writing verb — create, change, tag, delete, pass, re-trust — is
#     scoped to the roles the FLIP root owns, by literal name
#     (var.managed_role_names), and instance profiles likewise; the apply cannot
#     create a role of any other name, or touch any other role in the account;
#   * a role can only be created, or given a policy, if it carries the
#     permissions boundary — so a role the apply manages cannot be given IAM
#     write, and cannot mint anything more powerful than itself;
#   * only three named AWS-managed policies may be attached to anything, so
#     `AttachRolePolicy AdministratorAccess` is denied outright;
#   * an explicit Deny on both CI roles and on the boundary policy, so an apply
#     cannot re-trust itself or raise its own ceiling.
#
# What remains, stated plainly rather than claimed away: an apply can change the
# trust policy of one of FLIP's named roles, and so hand what that role holds to
# another principal. The control for that is the one that authorises the apply
# at all: review on the environment's branch, and the trust policy pinning
# job_workflow_ref to terraform_apply.yml at that branch.
data "aws_iam_policy_document" "apply_iam" {
  # checkov:skip=CKV_AWS_109:IAM write is the point of this document — the FLIP root owns the ECS task/execution, RDS proxy, Lambda and EC2 roles, so an apply cannot run without it; every write verb is scoped to those roles by name
  # checkov:skip=CKV_AWS_110:the role-mutation verbs are escalation primitives by nature; every one of them is scoped to the literally-named roles in var.managed_role_names, and the granting ones also to the boundary
  # checkov:skip=CKV_AWS_356:the "*" statements are IAM reads for refresh and service-linked-role creation; every write is scoped to named roles or instance profiles
  # No managed policy grants the IAM read verbs any more. Terraform refreshes
  # every aws_iam_role, aws_iam_role_policy, role-policy attachment and instance
  # profile in the FLIP root on each run, so without these an apply dies during
  # refresh, before it has a plan to gate. Read-only, and no wider than what the
  # plan role already holds through ReadOnlyAccess.
  statement {
    sid    = "ReadIamToRefresh"
    effect = "Allow"
    actions = [
      "iam:Get*",
      "iam:List*",
    ]
    resources = ["*"]
  }

  # IAM evaluates CreateRole against the ARN of the role being created, so it
  # can be scoped by name like any other role verb.
  statement {
    sid    = "CreateAndGrantOnlyInsideTheBoundary"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:PutRolePolicy",
    ]
    resources = local.managed_role_arns

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.permissions_boundary_arn]
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
    resources = local.managed_role_arns

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.permissions_boundary_arn]
    }

    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values   = local.attachable_policy_arns
    }
  }

  # Needed to put the boundary onto a role that predates it and to restore it if
  # someone strips it by hand. The condition means this can only ever set *this*
  # boundary, never a weaker one.
  statement {
    sid       = "SetTheBoundaryItself"
    effect    = "Allow"
    actions   = ["iam:PutRolePermissionsBoundary"]
    resources = local.managed_role_arns

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.permissions_boundary_arn]
    }
  }

  # Verbs that remove permissions or edit metadata. iam:TagRole is also required
  # by CreateRole whenever tags are set (the provider's default_tags).
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
    resources = local.managed_role_arns
  }

  # The two verbs that make a role usable by something else.
  statement {
    sid    = "PassAndRetrustOnlyTheKnownRoles"
    effect = "Allow"
    actions = [
      "iam:PassRole",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = local.managed_role_arns
  }

  # AddRoleToInstanceProfile is evaluated against the profile; putting a role in
  # one also needs iam:PassRole on that role, which is scoped above.
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
    resources = local.managed_instance_profile_arns
  }

  # Services create their own linked roles through the calling principal (ECS,
  # RDS, load balancing, CloudFront VPC origins). A service-linked role's
  # permissions are fixed by AWS, so creating one grants nothing to anyone else.
  statement {
    sid       = "ServiceLinkedRoles"
    effect    = "Allow"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]
  }

  # Belt and braces now that every Allow above is scoped to named roles that
  # never include these two: an operator widening managed_role_names by mistake
  # must still not hand the pipeline its own identity.
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
      local.apply_role_arn,
      local.plan_role_arn,
    ]
  }

  # A boundary an apply can rewrite is not a boundary. None of the Allows above
  # names a policy resource, so this is belt and braces — but it is the one object
  # whose integrity the rest of this document depends on, whether this module or
  # the platform declares it.
  statement {
    sid    = "NoBoundaryTampering"
    effect = "Deny"
    actions = [
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
    ]
    resources = [local.permissions_boundary_arn]
  }
}

resource "aws_iam_role_policy" "apply_iam" {
  name   = "flip-terraform-apply-iam"
  role   = aws_iam_role.terraform_apply.id
  policy = data.aws_iam_policy_document.apply_iam.json
}

# State access, spelled out: s3 is in the service allowlist only in the account's
# regions, and the lock object is named because a role that can write state but
# not the lock fails after the changes are made, not before.
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
