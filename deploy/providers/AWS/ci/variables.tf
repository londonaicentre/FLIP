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

# Required. terraform.tfvars.example lists them with placeholder values.

variable "aws_region" {
  description = "AWS region for the provider and the state bucket. The IAM resources are global."
  type        = string
}

variable "allowed_account_ids" {
  description = "The account this bootstrap is for. Terraform refuses to run against any other."
  type        = list(string)

  validation {
    condition     = length(var.allowed_account_ids) > 0
    error_message = "allowed_account_ids must name the account this bootstrap is for."
  }
}

variable "github_org" {
  description = "GitHub organisation owning the repository whose workflows may assume these roles."
  type        = string
}

variable "github_repo" {
  description = "GitHub repository whose workflows may assume these roles."
  type        = string
}

variable "environment" {
  description = "stag or prod. Only staging's plan role accepts pull-request merge refs."
  type        = string
}

variable "github_environment" {
  description = "GitHub Actions environment the jobs declare (e.g. aws-stag / aws-prod). Half of the OIDC trust policy."
  type        = string
}

variable "apply_branch" {
  description = "Branch whose pushes may assume the apply role (e.g. develop for stag, main for prod)."
  type        = string
}

variable "state_bucket_name" {
  description = "S3 bucket for the Terraform state — the FLIP root's and, after `make migrate-state`, this root's own."
  type        = string
}

# Optional. Defaults are the module's; see ../modules/terraform_ci_bootstrap/variables.tf
# for what each one does.

variable "drift_branch" {
  description = "Branch the drift workflow is loaded from (the repo default branch, unless the job is re-dispatched)."
  type        = string
  default     = "develop"
}

variable "state_key" {
  description = "Object key of the FLIP root's Terraform state."
  type        = string
  default     = "flip/terraform.tfstate"
}

variable "plan_workflow_file" {
  description = "Workflow file permitted to assume the plan role."
  type        = string
  default     = "terraform_plan.yml"
}

variable "apply_workflow_file" {
  description = "Workflow file permitted to assume the apply role."
  type        = string
  default     = "terraform_apply.yml"
}

variable "drift_workflow_file" {
  description = "Scheduled drift-detection workflow, which also plans."
  type        = string
  default     = "terraform_drift.yml"
}

variable "flip_api_secret_name" {
  description = "Name of the Secrets Manager secret the FLIP root manages, which the plan role may read."
  type        = string
  default     = "FLIP_API"
}

variable "managed_role_names" {
  description = "Names of the IAM roles the FLIP root owns, which an apply may pass and re-trust."
  type        = list(string)
  default = [
    "ecs-task-execution-role",
    "ecs-flip-api-task-role",
    "ecs-fl-api-task-role",
    "ecs-fl-server-task-role",
    "flip-rds-proxy-role",
    "flip-sg-drift-lambda-role",
    "ec2-role",
    "trust-ec2-role",
  ]
}

variable "attachable_managed_policies" {
  description = "AWS-managed policy names (path included) an apply may attach to a role."
  type        = list(string)
  default = [
    "service-role/AmazonECSTaskExecutionRolePolicy",
    "AmazonSSMManagedInstanceCore",
    "CloudWatchAgentServerPolicy",
  ]
}

variable "permissions_boundary_name" {
  description = "Name of the permissions-boundary policy. The FLIP root's iam_permissions_boundary_name must match it."
  type        = string
  default     = "AICentre-FLIPTerraformBoundary"
}

variable "plan_role_name" {
  description = "Name of the read-only plan role."
  type        = string
  default     = "AICentre-FLIPTerraformPlanRole"
}

variable "apply_role_name" {
  description = "Name of the apply role. The Makefile's APPLY_ROLE_NAME must match it."
  type        = string
  default     = "AICentre-FLIPTerraformApplyRole"
}

variable "tags" {
  description = "Tags applied to everything this root creates."
  type        = map(string)
  default = {
    ManagedBy = "terraform"
    Component = "terraform-ci"
  }
}

variable "manage_state_bucket" {
  description = "Create and harden the state bucket. Set false only if something else already manages it."
  type        = bool
  default     = true
}

variable "state_bucket_sse_algorithm" {
  description = "Default encryption for the state bucket: AES256 (SSE-S3), or aws:kms with the AWS-managed aws/s3 key."
  type        = string
  default     = "AES256"
}

variable "state_noncurrent_versions_retained" {
  description = "How many previous versions of each state object to keep."
  type        = number
  default     = 5
}

variable "state_noncurrent_version_days" {
  description = "How many days a previous state version is kept before it may expire."
  type        = number
  default     = 90
}

variable "restrict_state_writes" {
  description = "Deny state-object writes to every principal except the apply role and state_writer_principal_arns."
  type        = bool
  default     = false
}

variable "state_writer_principal_arns" {
  description = "Extra principals (aws:PrincipalArn patterns) allowed to write state when restrict_state_writes is on."
  type        = list(string)
  default     = []
}
