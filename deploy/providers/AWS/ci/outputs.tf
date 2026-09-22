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

# These two ARNs are what the GitHub environment variables TF_PLAN_ROLE_ARN and
# TF_APPLY_ROLE_ARN must hold. Keeping them as outputs rather than hard-coding
# account IDs in workflow YAML is what makes the LZA cutover (#749) a variable
# change: re-apply this root into the new account, repoint the two variables.
output "plan_role_arn" {
  description = "Role ARN for PR plans and drift detection. Set as TF_PLAN_ROLE_ARN on the GitHub environment."
  value       = aws_iam_role.terraform_plan.arn
}

output "apply_role_arn" {
  description = "Role ARN for applies. Set as TF_APPLY_ROLE_ARN on the GitHub environment."
  value       = aws_iam_role.terraform_apply.arn
}

output "account_id" {
  description = "Account these roles were created in — check it against the intended FLIP environment before wiring the ARNs up."
  value       = data.aws_caller_identity.current.account_id
}

# Printed so the trust policy can be diffed against what GitHub actually sends:
# `gh api /repos/{owner}/{repo}/actions/oidc/customization/sub` and the token's
# own claims. A mismatch here is the single most common cause of
# "Not authorized to perform sts:AssumeRoleWithWebIdentity" with no further detail.
output "expected_oidc_sub" {
  description = "The sub claim a job must present. Jobs must declare the matching GitHub environment to produce it."
  value       = local.oidc_sub
}

# The FLIP root composes this ARN itself, from the account ID and
# `var.iam_permissions_boundary_name`, rather than reading it from here — the two
# roots share no state. Printed so a mismatch is visible without an AWS call:
# if this does not equal what `terraform console` in ../ reports for
# `local.iam_permissions_boundary_arn`, every apply that creates a role fails.
output "permissions_boundary_arn" {
  description = "Permissions boundary every role the FLIP root creates must carry."
  value       = aws_iam_policy.apply_boundary.arn
}

output "expected_drift_job_workflow_ref" {
  description = "The job_workflow_ref the nightly drift run must present — the branch it is loaded from, not the apply branch."
  value       = "${local.workflow_ref_prefix}/${var.drift_workflow_file}@refs/heads/${var.drift_branch}"
}

output "expected_apply_job_workflow_ref" {
  description = "The job_workflow_ref claim the apply role requires — exactly this workflow file at exactly this ref."
  value       = local.apply_workflow_ref
}
