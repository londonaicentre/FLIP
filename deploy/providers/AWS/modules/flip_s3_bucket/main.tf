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

locals {
  # Build the list of policy statements. The HTTPS-only DenyHTTP statement is
  # always included; the MFA-delete statement is added only when
  # var.mfa_delete_protection is true. Both are merged into a single bucket
  # policy because S3 supports only one bucket policy document per bucket.
  bucket_policy_statements = concat(
    [
      {
        Sid       = "DenyHTTP"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.this.arn,
          "${aws_s3_bucket.this.arn}/*",
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
    ],
    var.mfa_delete_protection ? [
      {
        # `BoolIfExists` (not `Bool`) is deliberate: with plain `Bool`, a
        # request that omits the `aws:MultiFactorAuthPresent` context key
        # entirely produces no match, so the Deny would never fire and
        # the version delete would slip through. `BoolIfExists` treats an
        # absent key as if it were `false`, which is what we want here —
        # any caller who can't prove MFA is denied.
        Sid       = "RequireMFADeleteObjectVersion"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:DeleteObjectVersion"
        Resource  = "${aws_s3_bucket.this.arn}/*"
        Condition = {
          BoolIfExists = {
            "aws:MultiFactorAuthPresent" = "false"
          }
        }
      },
    ] : [],
  )
}

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
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
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

# Bucket policy — enforce HTTPS-only access and optionally require MFA for
# DeleteObjectVersion. Both statements live in a single bucket policy document
# because S3 supports only one bucket policy per bucket. The DenyHTTP statement
# denies every S3 action (`s3:*`) when the request is over plain HTTP
# (defense-in-depth alongside SSE-KMS at rest, and the broadest action set
# satisfies AWS Config's `S3_BUCKET_SSL_REQUESTS_ONLY` rule).
# The RequireMFADeleteObjectVersion statement (only when
# var.mfa_delete_protection is true) prevents attackers with stolen long-term
# credentials from destroying versioned data.
resource "aws_s3_bucket_policy" "this" {
  bucket = aws_s3_bucket.this.id

  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = local.bucket_policy_statements
  })
}

# Server access logging — delivers access logs to the central logging bucket.
# Skipped when logging_target_bucket is empty (default).
resource "aws_s3_bucket_logging" "this" {
  count = var.logging_target_bucket != "" ? 1 : 0

  bucket = aws_s3_bucket.this.id

  target_bucket = var.logging_target_bucket
  target_prefix = "${var.bucket_name}/"
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
