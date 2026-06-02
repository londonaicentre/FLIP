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
# This deploys the AWS services that cannot reasonably run locally — Cognito
# for auth, SES for email, and the three FLIP application S3 buckets — against
# the FLIP dev AWS account. Everything else (VPC, EC2, RDS, ALB, NLB, Route53,
# ACM, IAM, CloudWatch) is intentionally NOT in this stack; local development
# runs those services via Docker Compose.
#
# Why S3 is in dev: contract-level changes that depend on bucket policy or
# CORS (e.g. the presigned-PUT → presigned-POST migration in #438) used to
# pass on dev because Docker Compose doesn't preflight and `make e2e_smoke`
# uses python-requests (which also doesn't preflight). Folding the buckets
# into a shared module consumed by both dev and the stag/prod root means
# bucket policy / CORS changes plan identically across environments — the
# class of bug that surfaces only in a real browser at stag/prod is caught
# in dev plan output instead.
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

  user_pool_name           = var.flip_user_pool_name
  client_name              = var.flip_cognito_client
  sign_in_hostname         = var.sign_in_hostname
  admin_email              = var.flip_cognito_admin_email
  researcher_email         = var.flip_cognito_researcher_email
  admin_user_password      = var.ADMIN_USER_PASSWORD
  researcher_user_password = var.RESEARCHER_USER_PASSWORD
  templates_dir            = "${path.module}/../templates/cognito"
  callback_urls            = var.cognito_callback_urls
  logout_urls              = var.cognito_logout_urls
  mfa_configuration        = var.cognito_mfa_configuration
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

module "flip_model_files_uploads_bucket" {
  source      = "../modules/flip_s3_bucket"
  bucket_name = var.FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME
  # CORS mirrors the stag/prod root: `["PUT"]` today because flip-api currently
  # mints presigned PUT URLs (see flip-api/src/flip_api/file_services/
  # presigned_url_for_upload.py). When PR #438 lands and the upload flow
  # switches to presigned POST, narrow this to `["POST"]` in lockstep with
  # the stag/prod root — that's the dev-drift point this PR is closing.
  cors_methods         = ["PUT"]
  cors_allowed_origins = var.s3_cors_allowed_origins
}

module "flip_fl_results_bucket" {
  source               = "../modules/flip_s3_bucket"
  bucket_name          = var.FLIP_FL_RESULTS_BUCKET_NAME
  cors_methods         = ["GET"]
  cors_allowed_origins = var.s3_cors_allowed_origins
}

module "flip_app_bundles_bucket" {
  source      = "../modules/flip_s3_bucket"
  bucket_name = var.FLIP_APP_BUNDLES_BUCKET_NAME
  # No CORS: server-only consumer (flip-api running in Docker Compose).
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

output "FlipModelFilesUploadsBucket" {
  value = module.flip_model_files_uploads_bucket.bucket_id
}

output "FlipFlResultsBucket" {
  value = module.flip_fl_results_bucket.bucket_id
}

output "FlipAppBundlesBucket" {
  value = module.flip_app_bundles_bucket.bucket_id
}
