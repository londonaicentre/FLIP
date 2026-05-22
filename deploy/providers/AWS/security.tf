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
# Covers: Authorize/RevokeSecurityGroup{Ingress,Egress},
# DeleteSecurityGroup, ModifySecurityGroupRules,
# UpdateSecurityGroupRuleDescriptions{Ingress,Egress}, CreateSecurityGroup,
# and CreateTags/DeleteTags on SG resources (mitigates tag-evasion).

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
# SQS DLQ for failed SG drift events
############################

resource "aws_sqs_queue" "sg_drift_dlq" {
  name                       = "flip-sg-drift-dlq"
  message_retention_seconds  = 1209600 # 14 days
  visibility_timeout_seconds = 30
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


def _collect_sg_ids(request_params, response_elements):
    """Collect all SG IDs from request and response parameters."""
    ids = []

    # requestParameters.groupId (Authorize/Revoke/ModifySecurityGroupRules, etc.)
    gid = request_params.get("groupId")
    if gid:
        ids.append(gid)

    # requestParameters.groupSet.items[].groupId (older API variant)
    for item in request_params.get("groupSet", {}).get("items", []):
        gid = item.get("groupId")
        if gid:
            ids.append(gid)

    # responseElements.groupId (CreateSecurityGroup)
    gid = response_elements.get("groupId")
    if gid:
        ids.append(gid)

    # requestParameters.resourcesSet.items[].resourceId (CreateTags/DeleteTags)
    for item in request_params.get("resourcesSet", {}).get("items", []):
        rid = item.get("resourceId", "")
        if rid.startswith("sg-"):
            ids.append(rid)

    return ids


def handler(event, context):
    detail = event.get("detail", {})
    event_name = detail.get("eventName", "")
    request_params = detail.get("requestParameters", {}) or {}
    response_elements = detail.get("responseElements", {}) or {}

    sg_ids = _collect_sg_ids(request_params, response_elements)
    if not sg_ids:
        return {"statusCode": 400, "body": "No SG ID found in event"}

    sns = boto3.client("sns")
    topic_arn = os.environ["SNS_TOPIC_ARN"]
    alerted = False

    for sg_id in sg_ids:
        try:
            sg = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]
        except ec2.exceptions.ClientError as e:
            # SG was deleted before we could check tags. For
            # DeleteSecurityGroup events, alert anyway — the delete itself
            # is the drift we need to know about.
            error_code = e.response["Error"]["Code"]
            if event_name == "DeleteSecurityGroup":
                sns.publish(
                    TopicArn=topic_arn,
                    Subject=f"SG deleted: {sg_id}",
                    Message=json.dumps(
                        {
                            "event": event.get("detail-type", ""),
                            "event_name": event_name,
                            "sg_id": sg_id,
                            "source_ip": detail.get("sourceIPAddress", ""),
                            "time": detail.get("eventTime", ""),
                            "user": detail.get("userIdentity", {}).get("arn", ""),
                            "event_id": detail.get("eventID", ""),
                            "error": f"{error_code}: SG not found (already deleted)",
                        },
                        indent=2,
                    ),
                )
                alerted = True
            continue

        tags = {t["Key"]: t["Value"] for t in sg.get("Tags", [])}
        if tags.get("FlipSG") != "true":
            continue

        sns.publish(
            TopicArn=topic_arn,
            Subject=f"SG drift detected on {sg_id}",
            Message=json.dumps(
                {
                    "event": event.get("detail-type", ""),
                    "event_name": event_name,
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
        alerted = True

    if alerted:
        return {"statusCode": 200, "body": "Alert published"}
    return {"statusCode": 200, "body": "Not a FLIP SG, skipping"}
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

  dead_letter_config {
    target_arn = aws_sqs_queue.sg_drift_dlq.arn
  }

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

  statement {
    sid       = "SendToDlq"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.sg_drift_dlq.arn]
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
        "DeleteSecurityGroup",
        "ModifySecurityGroupRules",
        "UpdateSecurityGroupRuleDescriptionsIngress",
        "UpdateSecurityGroupRuleDescriptionsEgress",
        "CreateSecurityGroup",
        "CreateTags",
        "DeleteTags",
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

############################
# CloudWatch alarm: DLQ depth
############################

resource "aws_cloudwatch_metric_alarm" "sg_drift_dlq_depth" {
  alarm_name          = "flip-sg-drift-dlq-depth"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = "300"
  statistic           = "Sum"
  threshold           = "0"
  alarm_description   = "SG drift DLQ has undelivered events — Lambda failed after EventBridge retry window"
  alarm_actions       = [aws_sns_topic.sg_drift.arn]
  dimensions = {
    QueueName = aws_sqs_queue.sg_drift_dlq.name
  }
}
