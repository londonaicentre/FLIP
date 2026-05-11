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

# FLIP application S3 bucket — one bucket per tenant (model file uploads, FL
# results, FL app bundles). Splitting the previously-shared flip_bucket into
# three purpose-built buckets lets each tenant carry the minimum CORS surface
# it needs. Each caller passes `cors_methods` for the tenant it's standing
# up — at the time of writing the model-files-uploads bucket is on `["PUT"]`
# pending PR #438 (presigned PUT → POST migration), fl-results is on
# `["GET"]`, and app-bundles passes an empty list (server-only).

resource "aws_s3_bucket" "this" {
  bucket = var.bucket_name
  lifecycle {
    # Hardcoded `true` is deliberate. Issue #24's design originally listed
    # `prevent_destroy` as a module variable, but Terraform refuses to
    # interpolate a variable into `prevent_destroy` — it must be a literal.
    # All three FLIP application buckets hold persistent state (uploaded
    # artefacts, training results, FL bundles) that we never want to drop
    # on a refactor, so `true` is the right value for every caller. If a
    # future ephemeral-env caller needs a destroyable bucket, fork this
    # module rather than parameterise (Terraform's grammar doesn't allow
    # `prevent_destroy = var.x`).
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = "Enabled"
  }
}

# CORS is only created when cors_methods is non-empty. Server-only buckets
# (e.g. FL app bundles, fetched exclusively by flip-api via boto3) get no
# CORS resource at all, so the bucket presents no `Access-Control-Allow-*`
# surface to a browser.
resource "aws_s3_bucket_cors_configuration" "this" {
  count = length(var.cors_methods) > 0 ? 1 : 0

  bucket = aws_s3_bucket.this.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = var.cors_methods
    allowed_origins = var.cors_allowed_origins
    expose_headers  = []
  }
}
