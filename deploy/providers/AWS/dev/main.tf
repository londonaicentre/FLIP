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

# Dev-account Terraform root.
#
# This deploys only the AWS services that cannot reasonably run locally
# (Cognito for auth, SES for email) against the FLIP dev AWS account.
# Everything else — VPC, EC2, RDS, ALB, NLB, Route53, ACM, S3, IAM,
# CloudWatch — is intentionally NOT in this stack; local development runs
# those services via Docker Compose.
#
# See README.md in this directory for the first-time import workflow that
# brings the manually-created dev Cognito pool under terraform management.

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
  region = var.AWS_REGION
}

module "cognito" {
  source = "../modules/cognito"

  user_pool_name     = var.flip_user_pool_name
  client_name        = var.flip_cognito_client
  sign_in_hostname   = var.sign_in_hostname
  admin_email        = var.flip_cognito_admin_email
  researcher_email   = var.flip_cognito_researcher_email
  seed_user_password = var.ADMIN_USER_PASSWORD
  templates_dir      = "${path.module}/../templates/cognito"
  callback_urls      = var.cognito_callback_urls
  logout_urls        = var.cognito_logout_urls
}

module "ses" {
  source = "../modules/ses"

  sender_email  = var.SES_VERIFIED_EMAIL
  templates_dir = "${path.module}/../templates/ses"
  # Dev lives in a different AWS account from prod, so SES template name
  # collisions are not a concern; leave the prefix empty to keep the same
  # logical names in both envs.
  template_name_prefix = ""
}

output "CognitoUserPoolId" {
  value = module.cognito.user_pool_id
}

output "CognitoAppClientId" {
  value = module.cognito.app_client_id
}

output "CognitoDomain" {
  value = module.cognito.domain
}

output "SesSenderIdentityArn" {
  value = module.ses.sender_identity_arn
}
