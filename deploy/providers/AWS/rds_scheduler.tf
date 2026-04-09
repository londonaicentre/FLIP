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
# RDS Scheduled Stop/Start
############################
#
# Stops the RDS instance at night and starts it in the morning to save costs.
# Disabled by default — set enable_rds_schedule = true to activate.
# Schedules use Europe/London timezone (handles GMT/BST automatically).
#
# Default schedule:
#   Stop:  20:00 every day
#   Start: 07:00 Monday–Friday
# This keeps the database off overnight and all weekend.

# Package the Lambda source code
data "archive_file" "rds_scheduler" {
  count       = var.enable_rds_schedule ? 1 : 0
  type        = "zip"
  source_file = "${path.module}/lambda/rds_scheduler.py"
  output_path = "${path.module}/.terraform/tmp/rds_scheduler.zip"
}

############################
# Lambda IAM Role
############################

resource "aws_iam_role" "rds_scheduler_lambda" {
  count = var.enable_rds_schedule ? 1 : 0
  name  = "flip-rds-scheduler-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy" "rds_scheduler_lambda" {
  count = var.enable_rds_schedule ? 1 : 0
  name  = "rds-stop-start"
  role  = aws_iam_role.rds_scheduler_lambda[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "rds:StopDBInstance",
          "rds:StartDBInstance",
          "rds:DescribeDBInstances"
        ]
        Resource = module.flip_db.db_instance_arn
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:log-group:/aws/lambda/flip-rds-scheduler:*"
      }
    ]
  })
}

############################
# Lambda Function
############################

resource "aws_lambda_function" "rds_scheduler" {
  count            = var.enable_rds_schedule ? 1 : 0
  function_name    = "flip-rds-scheduler"
  description      = "Stops and starts the FLIP RDS instance on a schedule to save costs"
  role             = aws_iam_role.rds_scheduler_lambda[0].arn
  handler          = "rds_scheduler.handler"
  runtime          = "python3.12"
  timeout          = 30
  filename         = data.archive_file.rds_scheduler[0].output_path
  source_code_hash = data.archive_file.rds_scheduler[0].output_base64sha256

  environment {
    variables = {
      DB_IDENTIFIER = module.flip_db.db_instance_identifier
    }
  }
}

############################
# EventBridge Scheduler IAM
############################

resource "aws_iam_role" "rds_scheduler_eventbridge" {
  count = var.enable_rds_schedule ? 1 : 0
  name  = "flip-rds-scheduler-eventbridge-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "scheduler.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy" "rds_scheduler_eventbridge" {
  count = var.enable_rds_schedule ? 1 : 0
  name  = "invoke-lambda"
  role  = aws_iam_role.rds_scheduler_eventbridge[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.rds_scheduler[0].arn
    }]
  })
}

############################
# EventBridge Schedules
############################

resource "aws_scheduler_schedule" "rds_stop" {
  count       = var.enable_rds_schedule ? 1 : 0
  name        = "flip-rds-stop"
  description = "Stop FLIP RDS instance at night to save costs"

  schedule_expression          = var.rds_stop_cron
  schedule_expression_timezone = var.rds_schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.rds_scheduler[0].arn
    role_arn = aws_iam_role.rds_scheduler_eventbridge[0].arn
    input    = jsonencode({ action = "stop" })
  }
}

resource "aws_scheduler_schedule" "rds_start" {
  count       = var.enable_rds_schedule ? 1 : 0
  name        = "flip-rds-start"
  description = "Start FLIP RDS instance in the morning"

  schedule_expression          = var.rds_start_cron
  schedule_expression_timezone = var.rds_schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.rds_scheduler[0].arn
    role_arn = aws_iam_role.rds_scheduler_eventbridge[0].arn
    input    = jsonencode({ action = "start" })
  }
}
