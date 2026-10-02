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

# The module's outputs, unchanged. plan_role_arn and apply_role_arn are what the
# GitHub environment variables TF_PLAN_ROLE_ARN and TF_APPLY_ROLE_ARN hold.

output "plan_role_arn" {
  description = "Role ARN for PR plans and drift detection. Set as TF_PLAN_ROLE_ARN on the GitHub environment."
  value       = module.terraform_ci.plan_role_arn
}

output "apply_role_arn" {
  description = "Role ARN for applies. Set as TF_APPLY_ROLE_ARN on the GitHub environment."
  value       = module.terraform_ci.apply_role_arn
}

output "account_id" {
  description = "Account these roles were created in."
  value       = module.terraform_ci.account_id
}

output "expected_oidc_sub" {
  description = "The sub claim a job must present. Jobs must declare the matching GitHub environment to produce it."
  value       = module.terraform_ci.expected_oidc_sub
}

output "permissions_boundary_arn" {
  description = "Permissions boundary every role the FLIP root creates must carry."
  value       = module.terraform_ci.permissions_boundary_arn
}

output "expected_drift_job_workflow_ref" {
  description = "The job_workflow_ref the nightly drift run must present."
  value       = module.terraform_ci.expected_drift_job_workflow_ref
}

output "expected_apply_job_workflow_ref" {
  description = "The job_workflow_ref claim the apply role requires."
  value       = module.terraform_ci.expected_apply_job_workflow_ref
}

output "plan_role_name" {
  description = "Name of the plan role."
  value       = module.terraform_ci.plan_role_name
}

output "apply_role_name" {
  description = "Name of the apply role."
  value       = module.terraform_ci.apply_role_name
}

output "permissions_boundary_name" {
  description = "Name of the permissions-boundary policy."
  value       = module.terraform_ci.permissions_boundary_name
}

output "plan_job_workflow_refs" {
  description = "Every job_workflow_ref pattern the plan role accepts."
  value       = module.terraform_ci.plan_job_workflow_refs
}

output "state_bucket_arn" {
  description = "ARN of the state bucket when this root manages it, otherwise null."
  value       = module.terraform_ci.state_bucket_arn
}
