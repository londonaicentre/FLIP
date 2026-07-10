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
# S3 server access logging destination bucket
#
# Single destination for all FLIP application bucket access logs. Each source
# bucket writes to a unique prefix (its own name) to disambiguate logs.
# Retention: 90 days for compliance auditing.
############################

resource "aws_s3_bucket" "flip_access_logs" {
  bucket = "flip-access-logs-${var.flip_alb_subdomain}"

  tags = {
    Name = "flip-access-logs"
  }
}

resource "aws_s3_bucket_public_access_block" "flip_access_logs" {
  bucket                  = aws_s3_bucket.flip_access_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enforce HTTPS-only access — denies every S3 action over plain HTTP.
# The broadest action set (`s3:*`) is deliberate: it matches the same
# pattern used on every other bucket in this module and satisfies
# `S3_BUCKET_SSL_REQUESTS_ONLY` cleanly. (An earlier revision listed
# four actions including `s3:HeadObject`, which AWS rejects as
# `MalformedPolicy` in bucket policies — HEAD requests are authorized
# under `s3:GetObject`, so `s3:HeadObject` is not a real S3 action.)
# Defense-in-depth: the S3 Log Delivery group (granted WRITE + READ_ACP
# via ACL below) uses HTTPS internally, so this policy does not interfere
# with server access log delivery.
resource "aws_s3_bucket_policy" "flip_access_logs_https_only" {
  bucket = aws_s3_bucket.flip_access_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyHTTP"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.flip_access_logs.arn,
        "${aws_s3_bucket.flip_access_logs.arn}/*",
      ]
      Condition = {
        Bool = {
          "aws:SecureTransport" = "false"
        }
      }
    }]
  })
}

# AES256 (SSE-S3) is deliberate: S3 server access log delivery does not
# support SSE-KMS on the destination bucket, so this bucket cannot use the
# project's KMS CMK (`flip_app_key`) that every other application bucket uses.
# See: https://docs.aws.amazon.com/AmazonS3/latest/userguide/enable-server-access-logging.html#server-access-logging-overview
resource "aws_s3_bucket_server_side_encryption_configuration" "flip_access_logs" {
  bucket = aws_s3_bucket.flip_access_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "flip_access_logs" {
  bucket = aws_s3_bucket.flip_access_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "flip_access_logs" {
  bucket = aws_s3_bucket.flip_access_logs.id

  rule {
    id     = "expire-access-logs-after-90-days"
    status = "Enabled"

    filter {}

    expiration {
      days = 90
    }
  }
}

# Grant S3 Log Delivery group write access.
# `data.aws_canonical_user_id.current` is declared in data.tf.
resource "aws_s3_bucket_ownership_controls" "flip_access_logs" {
  bucket = aws_s3_bucket.flip_access_logs.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_acl" "flip_access_logs" {
  depends_on = [aws_s3_bucket_ownership_controls.flip_access_logs]
  bucket     = aws_s3_bucket.flip_access_logs.id

  access_control_policy {
    owner {
      id = data.aws_canonical_user_id.current.id
    }

    grant {
      grantee {
        type = "CanonicalUser"
        id   = data.aws_canonical_user_id.current.id
      }
      permission = "FULL_CONTROL"
    }

    # S3 Log Delivery group
    grant {
      grantee {
        type = "Group"
        uri  = "http://acs.amazonaws.com/groups/s3/LogDelivery"
      }
      permission = "WRITE"
    }

    grant {
      grantee {
        type = "Group"
        uri  = "http://acs.amazonaws.com/groups/s3/LogDelivery"
      }
      permission = "READ_ACP"
    }
  }
}
