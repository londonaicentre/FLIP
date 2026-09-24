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

# The Terraform-CI bootstrap for one FLIP workload account (FLIP#962, FLIP#1199):
#
#   AICentre-FLIPTerraformPlanRole   read-only; assumed by PR plans and the
#                                    nightly drift run. Cannot write state.
#   AICentre-FLIPTerraformApplyRole  assumed only by a push-triggered apply on
#                                    the environment's branch.
#   AICentre-FLIPTerraformBoundary   the ceiling on every role an apply creates.
#   the Terraform state bucket       optional (manage_state_bucket).
#
# A PUBLISHED INTERFACE, not a private module of the FLIP root. It is consumed by
# the platform repositories — aicentre-iac for the self-contained accounts,
# aicentre-lza-iac for the LZA ones — pinned to a FLIP commit SHA, and by ../../ci
# for anyone bootstrapping their own account. The point of that arrangement is
# separation of duties: this is the ceiling on FLIP's own pipeline, so a FLIP merge
# can only *propose* a change to it; nothing reaches an AI Centre account until a
# platform repository bumps the pinned SHA and its reviewers approve the plan.
# Renaming an input or changing a default is a breaking change for those callers.
#
# Deliberately not joined to the LZA cross-account chain: the LZA management role
# trusts `repo:${org}/${repo}:*` with AdministratorAccess, and adding FLIP to that
# chain would hand org-wide admin to every branch of a public repository. These
# roles are FLIP-scoped and live in the FLIP accounts.
#
# The GitHub OIDC identity provider is an input, never created here: an account
# holds one provider per issuer URL, shared by everything GitHub-driven in it, so
# it belongs to whatever owns the account's baseline IAM.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

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

  # The CI roles' and the boundary's own ARNs, composed rather than read from the
  # resources. A policy document that references a resource attribute becomes
  # "known after apply" whenever that resource has ANY pending change — even a tag
  # added by a caller's provider default_tags — and a plan then shows the whole
  # document as unknown, so a reviewer cannot see that it is unchanged. Composed,
  # the documents render at plan time. Default IAM path "/" throughout.
  plan_role_arn            = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${var.plan_role_name}"
  apply_role_arn           = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${var.apply_role_name}"
  permissions_boundary_arn = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/${var.permissions_boundary_name}"
}
