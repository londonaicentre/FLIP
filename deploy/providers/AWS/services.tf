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

############################
# FLIP application S3 buckets
############################
#
# The previous single `aws_s3_bucket.flip_bucket` held three tenants with
# materially different access patterns:
#
#   - model file uploads (researcher → browser POST → AV-scan → flip-api reads)
#   - FL results (fl-server writes; researcher downloads via browser GET)
#   - FL app bundles (server-only — flip-api copies base → destination)
#
# CORS is bucket-wide in S3, so colocating these three tenants forced any
# CORS change to widen across every consumer. Splitting into three buckets
# gives each tenant the minimum CORS surface it needs:
#
#   - flip-model-files-uploads: CORS POST (presigned POST policy enforces a
#     content-length-range cap and optional Content-Type at the S3 edge —
#     see flip-api/src/flip_api/utils/s3_client.py::get_put_presigned_post)
#   - flip-fl-results: CORS GET (browser presigned download)
#   - flip-app-bundles: no CORS resource (server-only, never browser-direct)
#
# State migration: the old `aws_s3_bucket.flip_bucket` resource and its
# sub-resources are dropped from this stack via `removed` blocks at the
# bottom of this file. The underlying AWS bucket is left in place so its
# contents can be migrated and verified out-of-band (`make migrate-flip-bucket`)
# before manual decommission.

module "flip_model_files_uploads_bucket" {
  source      = "./modules/flip_s3_bucket"
  bucket_name = var.FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME
  # Researcher uploads use a presigned POST policy minted by
  # `get_put_presigned_post` in
  # flip-api/src/flip_api/file_services/presigned_url_for_upload.py. The
  # policy bakes in `["content-length-range", 0, MAX_MODEL_FILE_BYTES]`
  # (and locks Content-Type when the caller supplies one), so S3 rejects
  # oversized or wrong-type uploads at the edge — the hub never sees them.
  cors_methods         = ["POST"]
  cors_allowed_origins = ["https://${var.flip_alb_subdomain}"]
  kms_key_arn          = aws_kms_key.flip_app_key.arn
}

module "flip_fl_results_bucket" {
  source               = "./modules/flip_s3_bucket"
  bucket_name          = var.FLIP_FL_RESULTS_BUCKET_NAME
  cors_methods         = ["GET"]
  cors_allowed_origins = ["https://${var.flip_alb_subdomain}"]
  kms_key_arn          = aws_kms_key.flip_app_key.arn
}

module "flip_app_bundles_bucket" {
  source      = "./modules/flip_s3_bucket"
  bucket_name = var.FLIP_APP_BUNDLES_BUCKET_NAME
  # No CORS: flip-api is the only consumer and reaches the bucket via boto3.
  kms_key_arn = aws_kms_key.flip_app_key.arn
}

############################
# AI Centre S3 Bucket
############################

resource "aws_s3_bucket" "aicentre_bucket" {
  bucket = var.AICENTRE_BUCKET_NAME
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "aicentre_bucket" {
  bucket                  = aws_s3_bucket.aicentre_bucket.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "aicentre_bucket" {
  bucket = aws_s3_bucket.aicentre_bucket.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.flip_app_key.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "aicentre_bucket" {
  bucket = aws_s3_bucket.aicentre_bucket.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_cors_configuration" "aicentre_bucket_cors" {
  bucket = aws_s3_bucket.aicentre_bucket.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT", "GET"]
    allowed_origins = ["https://${var.flip_alb_subdomain}"]
    expose_headers  = []
  }
}

############################
# Cognito
############################
#
# Resource definitions now live in ./modules/cognito. Existing state is
# migrated automatically by the `moved` blocks below on the next plan/apply
# — no manual `terraform state mv` step is required. See the module's
# header comment for the mapping from old root addresses to module paths.

module "cognito" {
  source = "./modules/cognito"

  user_pool_name     = var.flip_user_pool_name
  client_name        = var.flip_cognito_client
  sign_in_hostname   = var.flip_alb_subdomain
  admin_email        = var.flip_cognito_admin_email
  researcher_email   = var.flip_cognito_researcher_email
  seed_user_password = var.ADMIN_USER_PASSWORD
  templates_dir      = "${path.module}/templates/cognito"
  # The UI uses USER_SRP_AUTH (Cognito's native auth flow, not OAuth2 redirect),
  # so callback_urls are hygiene only — keep the canonical subdomain + localhost
  # for dev.
  callback_urls = ["https://${var.flip_alb_subdomain}", "https://localhost:443"]
  logout_urls   = ["https://${var.flip_alb_subdomain}", "https://localhost:443"]
}

# State migration: Cognito resources used to live at the root of this stack and
# now live inside module.cognito. These `moved` blocks let any state still on
# the old root addresses (or any fresh import that lands at the root) self-heal
# on the next plan — no manual `terraform state mv` step required. They are
# no-ops where state is already aligned. Safe to remove once every live state
# file has been migrated.
moved {
  from = aws_cognito_user_pool.flip_user_pool
  to   = module.cognito.aws_cognito_user_pool.flip_user_pool
}

moved {
  from = random_string.cognito_domain
  to   = module.cognito.random_string.cognito_domain
}

moved {
  from = aws_cognito_user_pool_domain.main
  to   = module.cognito.aws_cognito_user_pool_domain.main
}

moved {
  from = aws_cognito_user_pool_client.client
  to   = module.cognito.aws_cognito_user_pool_client.client
}

moved {
  from = aws_cognito_user.admin_user
  to   = module.cognito.aws_cognito_user.admin_user
}

# researcher_user is wrapped in `count = var.researcher_email == "" ? 0 : 1`
# inside the module, so the destination carries the [0] index.
moved {
  from = aws_cognito_user.researcher_user
  to   = module.cognito.aws_cognito_user.researcher_user[0]
}

############################
# Decommission of the old single-bucket layout
############################
#
# The previous design colocated three tenants under `aws_s3_bucket.flip_bucket`
# (see the head of this file for the rationale). These `removed` blocks tell
# Terraform to forget those resources from state without destroying the
# underlying AWS bucket — the bucket holds production data that is migrated
# out-of-band via `make migrate-flip-bucket` and then manually decommissioned
# (`aws s3 rb`) after a cooldown.
#
# `lifecycle { destroy = false }` is what makes this safe: without it,
# `removed` would attempt to delete the AWS resource on the next apply and
# the original `prevent_destroy = true` on `aws_s3_bucket.flip_bucket` would
# block the plan. Safe to drop these blocks once every live state file has
# been applied and the old bucket has been decommissioned in every env.
removed {
  from = aws_s3_bucket.flip_bucket
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket_public_access_block.flip_bucket
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket_server_side_encryption_configuration.flip_bucket
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket_versioning.flip_bucket
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_bucket_cors_configuration.flip_bucket_cors
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_object.app_destination_bucket
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_object.model_files
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_s3_object.uploaded_federated_data
  lifecycle {
    destroy = false
  }
}
