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

data "aws_caller_identity" "current" {}

############################
# ECS Task Execution Role
# Used by ECS agent to pull images, write logs, and fetch secrets/parameters
############################

resource "aws_iam_role" "ecs_task_execution_role" {
  name = "ecsTaskExecutionRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow execution role to read Parameter Store entries under /flip/*
resource "aws_iam_role_policy" "ecs_task_execution_ssm" {
  name = "ecs-task-execution-ssm"
  role = aws_iam_role.ecs_task_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameters", "ssm:GetParameter"]
      Resource = "arn:aws:ssm:${var.AWS_REGION}:${data.aws_caller_identity.current.account_id}:parameter/flip/*"
    }]
  })
}

# Allow execution role to read the FLIP_API secret and the RDS-managed DB secret
resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  name = "ecs-task-execution-secrets"
  role = aws_iam_role.ecs_task_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        module.flip_api_secret.secret_arn,
        module.flip_db.db_instance_master_user_secret_arn
      ]
    }]
  })
}

############################
# ECS Task Role
# Assumed by the running container; grants access to AWS services at runtime
############################

resource "aws_iam_role" "ecs_task_role" {
  name = "ecsTaskRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

# Scoped task role permissions — derived from actual boto3 calls in service source code.
# S3: flip-api reads/writes model files, FL data, app artefacts from flip_bucket and aicentre_bucket.
# Secrets Manager: flip-api reads FLIP_API (AES key, trust hashes) and the RDS-managed password.
# Cognito: flip-api manages users via list_users, admin_create/delete/enable/disable_user, revoke_token.
# SES: flip-api sends email via sesv2.send_email for access requests and imaging notifications.
# CloudWatch Logs: all services write structured logs to their respective log groups.
resource "aws_iam_role_policy" "ecs_task_role_scoped" {
  name = "ecs-task-scoped"
  role = aws_iam_role.ecs_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3FlipBuckets"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${var.FLIP_BUCKET_NAME}",
          "arn:aws:s3:::${var.FLIP_BUCKET_NAME}/*",
          "arn:aws:s3:::${var.AICENTRE_BUCKET_NAME}",
          "arn:aws:s3:::${var.AICENTRE_BUCKET_NAME}/*"
        ]
      },
      {
        Sid    = "SecretsManagerFlipRead"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          module.flip_api_secret.secret_arn,
          module.flip_db.db_instance_master_user_secret_arn
        ]
      },
      {
        Sid    = "CognitoFlipUserPool"
        Effect = "Allow"
        Action = [
          "cognito-idp:ListUsers",
          "cognito-idp:AdminCreateUser",
          "cognito-idp:AdminDeleteUser",
          "cognito-idp:AdminEnableUser",
          "cognito-idp:AdminDisableUser",
          "cognito-idp:RevokeToken"
        ]
        Resource = module.cognito.user_pool_arn
      },
      {
        Sid    = "SESSendEmail"
        Effect = "Allow"
        Action = [
          "ses:SendEmail",
          "ses:SendRawEmail",
          "sesv2:SendEmail"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = [
          "${aws_cloudwatch_log_group.ecs_flip_api.arn}:*",
          "${aws_cloudwatch_log_group.ecs_trust_api.arn}:*",
          "${aws_cloudwatch_log_group.ecs_imaging_api.arn}:*",
          "${aws_cloudwatch_log_group.ecs_data_access_api.arn}:*"
        ]
      }
    ]
  })
}
