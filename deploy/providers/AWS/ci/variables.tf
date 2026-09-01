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

variable "AWS_REGION" {
  description = "AWS region for the provider. The IAM resources here are global; this only selects the endpoint."
  type        = string
}

variable "environment" {
  description = "Which FLIP environment's account this root is being applied to."
  type        = string

  validation {
    condition     = contains(["stag", "prod"], var.environment)
    error_message = "environment must be 'stag' or 'prod'."
  }
}

variable "github_org" {
  description = "GitHub organisation owning the repository whose workflows may assume these roles."
  type        = string
  default     = "londonaicentre"
}

variable "github_repo" {
  description = "GitHub repository whose workflows may assume these roles."
  type        = string
  default     = "FLIP"
}

# The GitHub environment a job must declare to reach this account's secrets. It
# is also what the OIDC `sub` claim carries, so it is half of the trust policy:
# a job that does not declare it gets a token these roles will not accept.
variable "github_environment" {
  description = "GitHub Actions environment name holding this account's Terraform inputs (aws-stag / aws-prod)."
  type        = string
}

# The branch an apply is allowed to run from. Enforced twice, on purpose:
# here via the job_workflow_ref claim (IAM-side, cannot be edited by a PR), and
# in GitHub via the environment's deployment branch policy (platform-side, stops
# the job before it ever mints a token).
variable "apply_branch" {
  description = "Branch whose pushes may assume the apply role (develop for stag, main for prod)."
  type        = string
}

variable "state_bucket_name" {
  description = "S3 bucket holding this environment's Terraform state."
  type        = string
}

variable "state_key" {
  description = "Object key of the main Terraform state this CI role plans and applies."
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

variable "tags" {
  description = "Tags applied to every resource in this root."
  type        = map(string)
  default = {
    ManagedBy = "terraform"
    Component = "terraform-ci"
  }
}
