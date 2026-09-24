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

# Terraform-CI bootstrap for an account you own: the GitHub OIDC plan and apply
# roles, the permissions boundary, and the Terraform state bucket, all from
# ../modules/terraform_ci_bootstrap.
#
# For outside adopters only. AI Centre's accounts are bootstrapped by the platform
# repositories (aicentre-iac, aicentre-lza-iac), which instantiate the same module
# pinned to a FLIP commit; running this root in one of them would create a second
# owner for the same roles.
#
# State is local on the first apply — the bucket it will live in is one of the
# things this root creates — and moves into that bucket with `make migrate-state`.
# See README.md.

terraform {
  required_version = ">= 1.13.1"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  # The account guard. Terraform refuses to plan against any account not listed,
  # so a stale profile cannot create CI roles in the wrong estate.
  allowed_account_ids = var.allowed_account_ids

  default_tags {
    tags = var.tags
  }
}

# Looked up, never declared. An account holds one provider per issuer URL, shared
# by anything GitHub-driven in it, so it belongs to whatever owns the account's
# baseline IAM: declared here it would fail with EntityAlreadyExists where one
# exists, and a later `terraform destroy` of this root would delete a provider
# other workflows depend on. `make plan` checks for it first and, if it is
# missing, says how to declare it (see check-oidc-provider in the Makefile).
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

module "terraform_ci" {
  source = "../modules/terraform_ci_bootstrap"

  oidc_provider_arn  = data.aws_iam_openid_connect_provider.github.arn
  github_org         = var.github_org
  github_repo        = var.github_repo
  environment        = var.environment
  github_environment = var.github_environment
  apply_branch       = var.apply_branch
  state_bucket_name  = var.state_bucket_name

  drift_branch                = var.drift_branch
  state_key                   = var.state_key
  plan_workflow_file          = var.plan_workflow_file
  apply_workflow_file         = var.apply_workflow_file
  drift_workflow_file         = var.drift_workflow_file
  flip_api_secret_name        = var.flip_api_secret_name
  managed_role_names          = var.managed_role_names
  attachable_managed_policies = var.attachable_managed_policies
  permissions_boundary_name   = var.permissions_boundary_name
  plan_role_name              = var.plan_role_name
  apply_role_name             = var.apply_role_name
  tags                        = var.tags

  manage_state_bucket                = var.manage_state_bucket
  state_bucket_sse_algorithm         = var.state_bucket_sse_algorithm
  state_noncurrent_versions_retained = var.state_noncurrent_versions_retained
  state_noncurrent_version_days      = var.state_noncurrent_version_days
  restrict_state_writes              = var.restrict_state_writes
  state_writer_principal_arns        = var.state_writer_principal_arns
}

# These resources used to be declared directly in this root. The moves keep a
# state written by that layout at zero diff: Terraform re-addresses each object
# into the module instead of destroying and re-creating it. They cost nothing on a
# fresh state, where there is nothing to move.
moved {
  from = aws_iam_role.terraform_plan
  to   = module.terraform_ci.aws_iam_role.terraform_plan
}

moved {
  from = aws_iam_role_policy_attachment.plan_read_only
  to   = module.terraform_ci.aws_iam_role_policy_attachment.plan_read_only
}

moved {
  from = aws_iam_role_policy.plan_deny_state_writes
  to   = module.terraform_ci.aws_iam_role_policy.plan_deny_state_writes
}

moved {
  from = aws_iam_role_policy.plan_read_flip_api_secret
  to   = module.terraform_ci.aws_iam_role_policy.plan_read_flip_api_secret
}

moved {
  from = aws_iam_role.terraform_apply
  to   = module.terraform_ci.aws_iam_role.terraform_apply
}

moved {
  from = aws_iam_role_policy_attachment.apply_power_user
  to   = module.terraform_ci.aws_iam_role_policy_attachment.apply_power_user
}

moved {
  from = aws_iam_role_policy.apply_iam
  to   = module.terraform_ci.aws_iam_role_policy.apply_iam
}

moved {
  from = aws_iam_role_policy.apply_state
  to   = module.terraform_ci.aws_iam_role_policy.apply_state
}

moved {
  from = aws_iam_policy.apply_boundary
  to   = module.terraform_ci.aws_iam_policy.apply_boundary
}
