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
# The Terraform state bucket (optional)
############################
#
# The bucket holding the FLIP root's state in this account. Until this module,
# nothing declared it: three were made by scripts/create-backend.sh and one by
# hand, and they had drifted (three SSE-S3, one SSE-KMS; one carried a
# hand-added policy granting another account write access). Declaring it here
# makes its settings reviewed and consistent. The IAM policies above address it
# by name, not through these resources, so they render the same whether or not
# this module manages the bucket.
#
# prevent_destroy is literal because Terraform does not allow it to be a
# variable. Removing a caller's module block therefore needs a `removed { ...
# lifecycle { destroy = false } }` block, never a plain delete — which is the
# point: the state of every FLIP environment lives here.

resource "aws_s3_bucket" "state" {
  count = var.manage_state_bucket ? 1 : 0

  bucket = var.state_bucket_name
  tags   = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  count = var.manage_state_bucket ? 1 : 0

  bucket = aws_s3_bucket.state[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  count = var.manage_state_bucket ? 1 : 0

  bucket                  = aws_s3_bucket.state[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SSE-S3 by default. The AWS-managed aws/s3 KMS key adds no access control —
# its key policy lets any principal in the account decrypt through S3 — so it
# costs KMS calls for nothing; a customer-managed key would mean key-policy work
# and kms:Decrypt for the plan role, and is out of scope. `aws:kms` is still
# accepted so an existing bucket already using it imports without a diff.
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  count = var.manage_state_bucket ? 1 : 0

  bucket = aws_s3_bucket.state[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = var.state_bucket_sse_algorithm
    }
    bucket_key_enabled = var.state_bucket_sse_algorithm == "aws:kms"

    # Rejects uploads that bring their own key (SSE-C): state encrypted with a key
    # only the uploader holds is state nobody else can read back. Explicit because
    # the provider treats an unset value differently across versions — before
    # 6.66 it plans the live block's REMOVAL — and S3 now blocks SSE-C by default.
    blocked_encryption_types = ["SSE-C"]
  }
}

# Keeps a bounded history of every state object: the newest N previous versions,
# each for at most D days. The expired-delete-marker rule clears what native
# locking leaves behind — every lock release deletes `<key>.tflock`, and on a
# versioned bucket each delete is a marker. Staging usually wants a larger N than
# production, because laptop applies there churn through versions faster.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  count = var.manage_state_bucket ? 1 : 0

  bucket = aws_s3_bucket.state[0].id

  rule {
    id     = "terraform-state-history"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      newer_noncurrent_versions = var.state_noncurrent_versions_retained
      noncurrent_days           = var.state_noncurrent_version_days
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

# Who may WRITE state objects, when restrict_state_writes is on: the apply role,
# plus state_writer_principal_arns (engineers' SSO roles on staging, so laptop
# applies used to test a branch keep working; a break-glass role anywhere). The
# plan role already denies itself state writes in IAM. The platform's own
# Terraform role needs no entry — it manages this bucket's configuration, which
# a Deny on object writes does not touch.
locals {
  state_writer_arn_patterns = concat(
    [local.apply_role_arn],
    var.state_writer_principal_arns,
  )
}

data "aws_iam_policy_document" "state_bucket" {
  count = var.manage_state_bucket ? 1 : 0

  # Terraform state holds credentials in clear; it never travels unencrypted.
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [local.state_arn, local.state_all_objects_arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  # Built now and switched off by default, so enabling it later is a one-line
  # change per caller rather than a redesign. aws:PrincipalArn is the ROLE ARN for
  # an assumed-role session, so patterns name roles, with SSO paths included
  # (arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_<Set>_*).
  dynamic "statement" {
    for_each = var.restrict_state_writes ? [1] : []

    content {
      sid    = "DenyStateWritesExceptTheWriters"
      effect = "Deny"
      actions = [
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
        "s3:PutObject",
      ]
      resources = [local.state_all_objects_arn]

      principals {
        type        = "*"
        identifiers = ["*"]
      }

      condition {
        test     = "ArnNotLike"
        variable = "aws:PrincipalArn"
        values   = local.state_writer_arn_patterns
      }
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  count = var.manage_state_bucket ? 1 : 0

  bucket = aws_s3_bucket.state[0].id
  policy = data.aws_iam_policy_document.state_bucket[0].json

  # A policy on a bucket whose public-access block is still being set races it.
  depends_on = [aws_s3_bucket_public_access_block.state]
}
