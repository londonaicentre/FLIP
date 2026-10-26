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

# Opt-ins, all off by default.

# SSM interface endpoints, for a subnet with no internet route to SSM. Only the node may use them.
resource "aws_security_group" "ssm_endpoints" {
  count       = var.ssm_endpoints ? 1 : 0
  name        = "${var.name}-ssm-endpoints"
  description = "HTTPS from the FLIP trust node to its SSM endpoints"
  vpc_id      = module.network.vpc_id
  tags        = { Name = "${var.name}-ssm-endpoints" }
}

resource "aws_vpc_security_group_ingress_rule" "ssm_endpoints" {
  count                        = var.ssm_endpoints ? 1 : 0
  security_group_id            = aws_security_group.ssm_endpoints[0].id
  description                  = "HTTPS from the trust node"
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.node.id
}

resource "aws_vpc_endpoint" "ssm" {
  for_each            = var.ssm_endpoints ? toset(["ssm", "ssmmessages", "ec2messages"]) : toset([])
  vpc_id              = module.network.vpc_id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [module.network.subnet_id]
  security_group_ids  = [aws_security_group.ssm_endpoints[0].id]
  private_dns_enabled = true
  tags                = { Name = "${var.name}-${each.key}" }
}

# Stop/start schedule, for mock and test trusts only (see var.schedule_enabled).
data "aws_iam_policy_document" "scheduler_assume" {
  count = var.schedule_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  count                = var.schedule_enabled ? 1 : 0
  name                 = "${var.name}-scheduler"
  assume_role_policy   = data.aws_iam_policy_document.scheduler_assume[0].json
  permissions_boundary = local.boundary
}

resource "aws_iam_role_policy" "scheduler" {
  count = var.schedule_enabled ? 1 : 0
  name  = "start-stop-node"
  role  = aws_iam_role.scheduler[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ec2:StartInstances", "ec2:StopInstances"]
      Resource = [aws_instance.node.arn]
    }]
  })
}

resource "aws_scheduler_schedule" "node" {
  # checkov:skip=CKV_AWS_297:The schedule holds an instance id and a cron expression, nothing that needs a customer-managed key
  for_each = var.schedule_enabled ? {
    start = { expression = var.schedule_start, action = "startInstances" }
    stop  = { expression = var.schedule_stop, action = "stopInstances" }
  } : {}
  name                         = "${var.name}-${each.key}"
  schedule_expression          = each.value.expression
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:${each.value.action}"
    role_arn = aws_iam_role.scheduler[0].arn
    input    = jsonencode({ InstanceIds = [aws_instance.node.id] })
  }
}
