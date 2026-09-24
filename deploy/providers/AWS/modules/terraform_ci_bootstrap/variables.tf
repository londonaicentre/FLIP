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

variable "environment" {
  description = "Which FLIP environment's account this bootstrap is for. Decides whether pull-request plans may assume the plan role (stag only)."
  type        = string

  validation {
    condition     = contains(["stag", "prod"], var.environment)
    error_message = "environment must be 'stag' or 'prod'."
  }
}

# No default, on purpose. A default of londonaicentre/FLIP would make an outside
# adopter who forgot to set it create roles that trust AI Centre's workflows —
# and the staging plan role accepts pull-request merge refs.
variable "github_org" {
  description = "GitHub organisation owning the repository whose workflows may assume these roles."
  type        = string

  validation {
    condition     = length(var.github_org) > 0
    error_message = "github_org must be set."
  }
}

variable "github_repo" {
  description = "GitHub repository whose workflows may assume these roles."
  type        = string

  validation {
    condition     = length(var.github_repo) > 0
    error_message = "github_repo must be set."
  }
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

# The branch the *drift* workflow is loaded from, which is NOT `apply_branch`.
#
# GitHub only ever fires a `schedule` from the repository's default branch, so a
# scheduled job presents `terraform_drift.yml@refs/heads/<default branch>`
# whatever environment it targets. Deriving this from `apply_branch` is how the
# prod drift job silently stopped being able to assume anything: it presented
# `@refs/heads/develop` against a policy trusting `@refs/heads/main` only.
#
# Production sets this to `main` for a different reason than apply does: the
# nightly run on the default branch re-dispatches terraform_drift.yml onto
# `main` (see the workflow), so that `aws-prod` need not admit the default
# branch in its deployment branch policy. The two values coincide; the reasons
# do not, which is why this is its own variable.
variable "drift_branch" {
  description = "Branch the drift workflow is loaded from (the repo default branch, unless the job is re-dispatched)."
  type        = string
  default     = "develop"
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

variable "flip_api_secret_name" {
  description = <<-EOT
    Name of the Secrets Manager secret the FLIP root manages
    (module.flip_api_secret in ../../main.tf). The plan role needs an explicit read
    grant on it — see plan_read_flip_api_secret in main.tf. Kept as a variable
    rather than hardcoded so the coupling to the other root stays visible, and so
    a renamed secret is a one-line change here rather than a silent plan failure.
  EOT
  type        = string
  default     = "FLIP_API"
}

# Every IAM role the FLIP root manages, by literal name. All of them are named
# rather than generated, which is what makes it possible to scope the escalation
# primitives — iam:PassRole and iam:UpdateAssumeRolePolicy — to a list instead of
# granting them on "*".
#
# Adding a role to the FLIP root therefore means adding it here, and the platform
# repositories bumping their pinned SHA and applying, BEFORE the FLIP change that
# creates the role — or the apply that creates it cannot pass or re-trust it. That coupling is deliberate: it puts a human in the loop on every
# new principal the pipeline can hand to a service.
variable "managed_role_names" {
  description = "Names of the IAM roles the FLIP root owns, which an apply may pass and re-trust."
  type        = list(string)
  default = [
    # iam_ecs.tf
    "ecs-task-execution-role",
    "ecs-flip-api-task-role",
    "ecs-fl-api-task-role",
    "ecs-fl-server-task-role",
    # rds_proxy.tf
    "flip-rds-proxy-role",
    # security.tf
    "flip-sg-drift-lambda-role",
    # main.tf, via terraform-aws-modules/iam//modules/iam-assumable-role
    "ec2-role",
    "trust-ec2-role",
  ]
}

# The only AWS-managed policies the FLIP root attaches to anything. Bound to
# iam:AttachRolePolicy as an iam:PolicyARN condition, so an apply cannot attach
# AdministratorAccess (or anything else) to a role it has just created.
variable "attachable_managed_policies" {
  description = "AWS-managed policy names (path included) an apply may attach to a role."
  type        = list(string)
  default = [
    "service-role/AmazonECSTaskExecutionRolePolicy", # iam_ecs.tf, execution role
    "AmazonSSMManagedInstanceCore",                  # main.tf, both EC2 roles
    "CloudWatchAgentServerPolicy",                   # main.tf, trust EC2 role
  ]
}

# The permissions boundary every role an apply creates must carry. Declared by
# this module and referenced by name from the FLIP root, which sets it on each of
# its roles — see `iam_permissions_boundary_name` there.
variable "permissions_boundary_name" {
  description = "Name of the managed policy used as the permissions boundary on roles the pipeline creates."
  type        = string
  default     = "AICentre-FLIPTerraformBoundary"
}

variable "tags" {
  description = "Tags applied to the roles, the boundary and the state bucket."
  type        = map(string)
  default = {
    ManagedBy = "terraform"
    Component = "terraform-ci"
  }
}

variable "oidc_provider_arn" {
  description = "ARN of the account's GitHub Actions OIDC identity provider (token.actions.githubusercontent.com). Owned by whatever manages the account's baseline IAM — never by this module."
  type        = string

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:oidc-provider/token\\.actions\\.githubusercontent\\.com$", var.oidc_provider_arn))
    error_message = "oidc_provider_arn must be the ARN of a token.actions.githubusercontent.com OIDC provider."
  }
}

# Defaults are the live names in every AI Centre account. Renaming them there
# would orphan the GitHub environment variables holding their ARNs.
variable "plan_role_name" {
  description = "Name of the read-only plan role."
  type        = string
  default     = "AICentre-FLIPTerraformPlanRole"
}

variable "apply_role_name" {
  description = "Name of the apply role."
  type        = string
  default     = "AICentre-FLIPTerraformApplyRole"
}

variable "manage_state_bucket" {
  description = "Declare and harden the Terraform state bucket named by state_bucket_name (versioning, public-access block, encryption, lifecycle, TLS-only policy). False leaves the bucket to something else."
  type        = bool
  default     = true
}

variable "state_bucket_sse_algorithm" {
  description = "Default encryption for the state bucket: AES256 (SSE-S3), or aws:kms with the AWS-managed aws/s3 key."
  type        = string
  default     = "AES256"

  validation {
    condition     = contains(["AES256", "aws:kms"], var.state_bucket_sse_algorithm)
    error_message = "state_bucket_sse_algorithm must be AES256 or aws:kms."
  }
}

variable "state_noncurrent_versions_retained" {
  description = "How many previous versions of each state object to keep. Staging usually wants more than production: laptop applies there churn through versions."
  type        = number
  default     = 5

  validation {
    condition     = var.state_noncurrent_versions_retained >= 1 && var.state_noncurrent_versions_retained <= 100
    error_message = "state_noncurrent_versions_retained must be between 1 and 100 (the S3 limit)."
  }
}

variable "state_noncurrent_version_days" {
  description = "How many days a previous state version is kept before it may expire."
  type        = number
  default     = 90
}

variable "restrict_state_writes" {
  description = "Deny state-object writes to every principal except the apply role and state_writer_principal_arns. Off by default: enable per account once the rest of the bootstrap is proven."
  type        = bool
  default     = false
}

variable "state_writer_principal_arns" {
  description = "Extra principals allowed to write state when restrict_state_writes is on, as aws:PrincipalArn patterns (ArnLike). For an SSO role include its path: arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_<PermissionSet>_*. Staging typically lists the engineers' role so laptop applies keep working; production lists a break-glass role only."
  type        = list(string)
  default     = []
}
