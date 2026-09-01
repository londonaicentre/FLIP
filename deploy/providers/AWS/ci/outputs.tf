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

output "expected_apply_job_workflow_ref" {
  description = "The job_workflow_ref claim the apply role requires — exactly this workflow file at exactly this ref."
  value       = local.apply_workflow_ref
}
