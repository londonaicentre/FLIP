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

# Three task roles + one execution role. Per-service task roles enforce least
# privilege at the IAM layer: even if fl-server is compromised via an untrusted
# FL client connection, its role cannot read AES_KEY_BASE64, trust API key
# hashes, or any other flip-api-only secret because those ARNs are absent
# from its policy.

data "aws_caller_identity" "current" {}

############################
# Trust policy (shared)
############################

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

############################
# Execution role (shared by all three services)
############################
#
# The execution role pulls images from ECR, writes container logs to
# CloudWatch, and resolves task definition `secrets` references. It does NOT
# read application secrets at runtime — that is the task role's job.

resource "aws_iam_role" "ecs_task_execution" {
  name               = "ecs-task-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow the execution role to resolve `secrets` references in task definitions
# to specific Secrets Manager + SSM ARNs. Scoped to FLIP resources only —
# never Resource = "*".
data "aws_iam_policy_document" "ecs_task_execution_secrets" {
  statement {
    sid     = "ReadFlipApiSecret"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      module.flip_api_secret.secret_arn,
      module.flip_db.db_instance_master_user_secret_arn,
    ]
  }

  statement {
    sid     = "ReadFlipSsmParameters"
    actions = ["ssm:GetParameters", "ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${var.AWS_REGION}:${data.aws_caller_identity.current.account_id}:parameter/flip/*",
    ]
  }
}

resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  name   = "flip-ecs-task-execution-secrets"
  role   = aws_iam_role.ecs_task_execution.id
  policy = data.aws_iam_policy_document.ecs_task_execution_secrets.json
}

############################
# flip-api task role
############################
#
# flip-api needs full access to flip-api-only secrets, the user pool for
# Cognito admin operations, the verified SES identity for emails, and the
# FLIP S3 buckets for model file IO.

resource "aws_iam_role" "ecs_flip_api_task" {
  name               = "ecs-flip-api-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "ecs_flip_api_task" {
  statement {
    sid     = "ReadFlipApiSecret"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      module.flip_api_secret.secret_arn,
      module.flip_db.db_instance_master_user_secret_arn,
    ]
  }

  statement {
    sid = "CognitoUserPool"
    actions = [
      "cognito-idp:AdminCreateUser",
      "cognito-idp:AdminDeleteUser",
      "cognito-idp:AdminGetUser",
      "cognito-idp:AdminInitiateAuth",
      "cognito-idp:AdminRespondToAuthChallenge",
      "cognito-idp:AdminSetUserPassword",
      "cognito-idp:AdminUserGlobalSignOut",
      "cognito-idp:DescribeUserPool",
      "cognito-idp:DescribeUserPoolClient",
      "cognito-idp:ListUsers",
    ]
    resources = [module.cognito.user_pool_arn]
  }

  statement {
    sid       = "SesSend"
    actions   = ["ses:SendEmail", "ses:SendRawEmail", "ses:SendTemplatedEmail"]
    resources = [module.ses.sender_identity_arn]
  }

  statement {
    sid = "S3FlipBuckets"
    actions = [
      "s3:CopyObject",
      "s3:DeleteObject",
      "s3:GetBucketLocation",
      "s3:GetObject",
      "s3:HeadObject",
      "s3:ListBucket",
      "s3:PutObject",
    ]
    resources = [
      aws_s3_bucket.flip_bucket.arn,
      "${aws_s3_bucket.flip_bucket.arn}/*",
      aws_s3_bucket.aicentre_bucket.arn,
      "${aws_s3_bucket.aicentre_bucket.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "ecs_flip_api_task" {
  name   = "flip-api-task-policy"
  role   = aws_iam_role.ecs_flip_api_task.id
  policy = data.aws_iam_policy_document.ecs_flip_api_task.json
}

############################
# fl-api task role
############################
#
# fl-api is internal-only and orchestrates FL training jobs against fl-server.
# It does not read application secrets and does not need S3, Cognito, or SES.
# CloudWatch Logs is granted via the execution role (LogConfiguration writes
# come from the agent, not the task role). Empty inline policy by design —
# extended in PR 2 only if a runtime call needs it.

resource "aws_iam_role" "ecs_fl_api_task" {
  name               = "ecs-fl-api-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

############################
# fl-server task role
############################
#
# fl-server is reachable from untrusted FL clients via the NLB. Its role is
# minimal: read its INTERNAL_SERVICE_KEY from the FLIP_API secret (so it can
# call back to flip-api on /api/model/{id}/status) and write training results
# to s3://${FLIP_BUCKET_NAME}/uploaded_federated_data/*. Crucially, it has
# NO access to AES_KEY_BASE64, TRUST_API_KEY_HASHES, or any flip-api-only
# data. The secret is shared today (single FLIP_API secret) so the
# execution role's GetSecretValue covers fetch; the task role here only
# needs to expose ListSecretVersionIds for runtime introspection if
# needed — kept empty until PR 2 wires actual runtime calls.

resource "aws_iam_role" "ecs_fl_server_task" {
  name               = "ecs-fl-server-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "ecs_fl_server_task" {
  statement {
    sid     = "S3UploadFederatedData"
    actions = ["s3:PutObject", "s3:GetObject", "s3:HeadObject", "s3:DeleteObject"]
    resources = [
      "${aws_s3_bucket.flip_bucket.arn}/uploaded_federated_data/*",
    ]
  }

  statement {
    sid       = "S3ListFlipBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.flip_bucket.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["uploaded_federated_data/*", "uploaded_federated_data"]
    }
  }
}

resource "aws_iam_role_policy" "ecs_fl_server_task" {
  name   = "fl-server-task-policy"
  role   = aws_iam_role.ecs_fl_server_task.id
  policy = data.aws_iam_policy_document.ecs_fl_server_task.json
}
