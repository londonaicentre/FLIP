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
# S3
############################

resource "aws_s3_bucket" "flip_bucket" {
  bucket = var.FLIP_BUCKET_NAME
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_cors_configuration" "flip_bucket_cors" {
  bucket = aws_s3_bucket.flip_bucket.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT"]
    allowed_origins = ["*"]
    expose_headers  = []
  }
}

# Model files uploaded by researchers will be combined with the base app here
resource "aws_s3_object" "app_destination_bucket" {
  bucket  = aws_s3_bucket.flip_bucket.id
  key     = "app_destination_bucket/"
  content = ""
  lifecycle {
    prevent_destroy = true
  }
}

# Model files uploaded by researchers will be stored here
resource "aws_s3_object" "model_files" {
  bucket  = aws_s3_bucket.flip_bucket.id
  key     = "model_files/"
  content = ""
  lifecycle {
    prevent_destroy = true
  }
}

# FL results will be stored here
resource "aws_s3_object" "uploaded_federated_data" {
  bucket  = aws_s3_bucket.flip_bucket.id
  key     = "uploaded_federated_data/"
  content = ""
  lifecycle {
    prevent_destroy = true
  }
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

resource "aws_s3_bucket_cors_configuration" "aicentre_bucket_cors" {
  bucket = aws_s3_bucket.aicentre_bucket.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT", "GET"]
    allowed_origins = ["*"]
    expose_headers  = []
  }
}

############################
# Cognito
############################

module "cognito" {
  source = "./modules/cognito"

  user_pool_name     = var.flip_user_pool_name
  client_name        = var.flip_cognito_client
  sign_in_hostname   = var.flip_alb_subdomain
  admin_email        = var.flip_cognito_admin_email
  researcher_email   = var.flip_cognito_researcher_email
  seed_user_password = var.ADMIN_USER_PASSWORD
  templates_dir      = "${path.module}/templates/cognito"
  # The UI uses USER_PASSWORD_AUTH (not OAuth redirect), so callback_urls are
  # hygiene only — keep the canonical subdomain + localhost for dev.
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
