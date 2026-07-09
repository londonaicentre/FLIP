# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

############################################################
# Cost monitoring for the MLflow integration (FLIP#745)
#
# The MLflow App itself advertises no compute charge, so the point of these
# alarms is NOT to track a bill we expect — it is to catch the two ways this
# integration could start costing real money:
#
#   1. Someone creates a *classic* MLflow tracking server (the sized, always-on
#      variant) instead of a serverless App: ~$0.64/hr ≈ $470/month. The FLIP
#      developer permission set cannot do this (it grants only
#      sagemaker:*MlflowApp*), but an account admin can, and a $5 budget makes
#      that visible within a day rather than at the next invoice.
#   2. AWS begins metering something that is free today — App compute, per-request
#      charges, or metadata storage above a free allowance. Apps have no published
#      rate card yet, which is exactly why the anomaly monitor exists: it alerts on
#      a *new* charge shape, not just on a threshold.
#
# Everything MLflow-related lands under the SageMaker service in Cost Explorer.
# Artifact-bucket storage is negligible by design (result zips are registered by
# S3 reference, never copied into MLflow) and is covered by the account's normal
# S3 spend.
#
# Budget alerts go straight to the admin address by email — no SNS topic, so
# there is no topic policy to keep in step. AWS Budgets is free for the first two
# budgets per account; this is the first.
############################################################

resource "aws_budgets_budget" "sagemaker_mlflow" {
  count = var.enable_mlflow_cost_alerts ? 1 : 0

  name         = "flip-sagemaker-mlflow"
  budget_type  = "COST"
  limit_amount = var.mlflow_monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "Service"
    values = ["Amazon SageMaker"]
  }

  # Fire early and on the forecast: an accidental tracking server blows a $5
  # budget on day one, and the forecast trips before the first full day of spend.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.SES_VERIFIED_EMAIL]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.SES_VERIFIED_EMAIL]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.SES_VERIFIED_EMAIL]
  }
}

# Cost Explorer is a global service reached through us-east-1 — reuse the alias
# CloudFront already needs (cloudfront.tf) rather than adding a second one.
resource "aws_ce_anomaly_monitor" "sagemaker" {
  count    = var.enable_mlflow_cost_alerts ? 1 : 0
  provider = aws.us_east_1

  name              = "flip-sagemaker-anomalies"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "sagemaker" {
  count    = var.enable_mlflow_cost_alerts ? 1 : 0
  provider = aws.us_east_1

  name             = "flip-sagemaker-anomalies"
  frequency        = "IMMEDIATE"
  monitor_arn_list = [aws_ce_anomaly_monitor.sagemaker[0].arn]

  subscriber {
    type    = "EMAIL"
    address = var.SES_VERIFIED_EMAIL
  }

  # IMMEDIATE subscriptions must carry a threshold expression. $1 is deliberately
  # low: at FLIP's expected ~$0 MLflow spend, any anomaly worth a dollar is worth
  # an email.
  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = ["1"]
    }
  }
}
