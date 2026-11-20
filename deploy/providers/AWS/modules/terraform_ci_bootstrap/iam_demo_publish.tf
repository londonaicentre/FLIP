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
# Demo-publish role (FLIP#2012)
############################

# The public demo is served from its own host and bucket, so the browser keeps it
# away from the app's tokens. Its publish gets its own role, scoped to that bucket:
# the demo job never holds credentials for the app's bucket, nor the app job for
# the demo's. Created only when the caller names the demo bucket. It trusts the
# same reusable workflow as the UI-publish role (iam_ui_publish.tf).
resource "aws_iam_role" "demo_publish" {
  count = local.demo_publish ? 1 : 0

  name                 = var.demo_publish_role_name
  description          = "Role that publishes the public demo to its S3 bucket from GitHub Actions on ${var.apply_branch} (FLIP#2012)"
  assume_role_policy   = data.aws_iam_policy_document.ui_publish_assume_role[0].json
  max_session_duration = 3600
  tags                 = var.tags
}

# The bucket is SSE-S3 (services.tf in the FLIP root), so no KMS grant.
data "aws_iam_policy_document" "demo_publish" {
  count = local.demo_publish ? 1 : 0

  statement {
    sid       = "ListTheDemoBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [local.demo_bucket_arn]
  }

  statement {
    sid    = "SyncTheDemoObjects"
    effect = "Allow"
    actions = [
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${local.demo_bucket_arn}/*"]
  }
}

resource "aws_iam_role_policy" "demo_publish" {
  count = local.demo_publish ? 1 : 0

  name   = "flip-demo-publish"
  role   = aws_iam_role.demo_publish[0].id
  policy = data.aws_iam_policy_document.demo_publish[0].json
}
