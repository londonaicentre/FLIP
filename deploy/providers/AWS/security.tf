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

# SG drift detection — monitors CloudTrail events for security group
# modifications on FlipSG-tagged SGs and publishes alerts to an SNS topic.
# Architecture: CloudTrail -> EventBridge -> Lambda (tag filter) -> SNS -> Email
#
# Known limitations:
# - Tag-based evasion: The Lambda reads tags at invocation time via
#   describe_security_groups. An attacker who can mutate an SG can also call
#   DeleteTags first to evade the filter. Mitigation would require subscribing
#   to CreateTags/DeleteTags events scoped to SG resources.
# - Missing event types: CreateSecurityGroup, DeleteSecurityGroup,
#   ModifySecurityGroupRules, UpdateSecurityGroupRuleDescriptions{Ingress,Egress},
#   and tag mutations (CreateTags/DeleteTags on SG resources) are not captured.
#   See the EventBridge rule below for the current scope.

############################
# SNS Topic for SG drift alerts
############################

resource "aws_sns_topic" "sg_drift" {
  name = "flip-sg-drift"
}

resource "aws_sns_topic_subscription" "sg_drift_email" {
  topic_arn = aws_sns_topic.sg_drift.arn
  protocol  = "email"
  endpoint  = var.SES_VERIFIED_EMAIL
}

############################
# Lambda: tag-filtered SG drift publisher
############################

data "archive_file" "sg_drift_lambda" {
  type        = "zip"
  output_path = "${path.module}/.terraform-build/sg_drift_lambda.zip"

  source {
    content  = <<-PYTHON
import boto3, json, os

ec2 = boto3.client("ec2")

def handler(event, context):
    detail = event.get("detail", {})
    request_params = detail.get("requestParameters", {})

    # SG ID can appear in requestParameters.groupId (console/modern API) or
    # requestParameters.groupSet.items[].groupId (older EC2 API calls).
    sg_id = request_params.get("groupId", "")
    if not sg_id:
        group_set = request_params.get("groupSet", {}).get("items", [])
        if group_set:
            sg_id = group_set[0].get("groupId", "")
    if not sg_id:
        return {"statusCode": 400, "body": "No SG ID found in event"}

    # Fetch tags to check if this is a FLIP-managed SG.
    try:
        sg = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]
    except Exception as e:
        return {"statusCode": 500, "body": f"Failed to describe SG {sg_id}: {e}"}

    tags = {t["Key"]: t["Value"] for t in sg.get("Tags", [])}
    if tags.get("FlipSG") != "true":
        return {"statusCode": 200, "body": "Not a FLIP SG, skipping"}

    # Publish alert.
    sns = boto3.client("sns")
    sns.publish(
        TopicArn=os.environ["SNS_TOPIC_ARN"],
        Subject=f"SG drift detected on {sg_id}",
        Message=json.dumps(
            {
                "event": event.get("detail-type", ""),
                "sg_id": sg_id,
                "sg_name": sg.get("GroupName", ""),
                "source_ip": detail.get("sourceIPAddress", ""),
                "time": detail.get("eventTime", ""),
                "user": detail.get("userIdentity", {}).get("arn", ""),
                "event_id": detail.get("eventID", ""),
            },
            indent=2,
        ),
    )
    return {"statusCode": 200, "body": "Alert published"}
PYTHON
    filename = "lambda_function.py"
  }
}

resource "aws_lambda_function" "sg_drift_filter" {
  filename         = data.archive_file.sg_drift_lambda.output_path
  function_name    = "flip-sg-drift-filter"
  role             = aws_iam_role.sg_drift_lambda_role.arn
  handler          = "lambda_function.handler"
  runtime          = "python3.12"
  architectures    = ["arm64"]
  source_code_hash = data.archive_file.sg_drift_lambda.output_base64sha256
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      SNS_TOPIC_ARN = aws_sns_topic.sg_drift.arn
    }
  }
}

############################
# IAM for Lambda
############################

resource "aws_iam_role" "sg_drift_lambda_role" {
  name               = "flip-sg-drift-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "sg_drift_lambda_policy" {
  statement {
    sid       = "DescribeSecurityGroups"
    actions   = ["ec2:DescribeSecurityGroups"]
    resources = ["*"]
  }

  statement {
    sid       = "PublishSns"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.sg_drift.arn]
  }

  # Basic Lambda logging — scoped to this function's log group.
  statement {
    sid = "WriteLogs"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:aws:logs:${var.AWS_REGION}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/flip-sg-drift-filter:*",
    ]
  }
}

resource "aws_iam_role_policy" "sg_drift_lambda_policy" {
  name   = "flip-sg-drift-lambda-policy"
  role   = aws_iam_role.sg_drift_lambda_role.id
  policy = data.aws_iam_policy_document.sg_drift_lambda_policy.json
}

############################
# EventBridge rule + target
############################

resource "aws_cloudwatch_event_rule" "sg_drift" {
  name        = "flip-sg-drift"
  description = "Capture SG modification events for FlipSG-tagged security groups"

  event_pattern = jsonencode({
    source = ["aws.ec2"]
    detail-type = [
      "AWS API Call via CloudTrail"
    ]
    detail = {
      eventSource = ["ec2.amazonaws.com"]
      eventName = [
        "AuthorizeSecurityGroupIngress",
        "AuthorizeSecurityGroupEgress",
        "RevokeSecurityGroupIngress",
        "RevokeSecurityGroupEgress",
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "sg_drift" {
  rule      = aws_cloudwatch_event_rule.sg_drift.name
  target_id = "sg-drift-filter-lambda"
  arn       = aws_lambda_function.sg_drift_filter.arn
}

resource "aws_lambda_permission" "sg_drift_allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sg_drift_filter.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.sg_drift.arn
}
