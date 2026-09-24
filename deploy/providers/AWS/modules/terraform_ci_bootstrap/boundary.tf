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
# The permissions boundary
############################
#
# Declared here, in the account bootstrap the pipeline does NOT apply, and set by the FLIP
# root on every role it owns (`iam_permissions_boundary_name`, ../../variables.tf).
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
  # checkov:skip=CKV_AWS_108:data-exfiltration verbs likewise — the FLIP task roles' own policies are the grant, and they are scoped in ../../iam_ecs.tf
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
  tags        = var.tags
}
