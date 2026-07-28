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

# The sealed account's lifelines: with no IGW or NAT, every AWS API this
# stack touches must be served by a VPC endpoint. Unlike the prod root
# (../vpc_endpoints.tf gates interface endpoints behind enable_ecs_endpoints
# to save the per-AZ hourly ENI charge), these are unconditional — nothing
# in this VPC works without them.
#
#   s3 (gateway)      — ECR stores image layers in S3; free, on the private
#                       route tables
#   ecr.api / ecr.dkr — image pulls by the Fargate tasks
#   logs              — awslogs log driver from the tasks
#   ssm / ssmmessages / ec2messages — SSM Session Manager to the probe

############################
# Security group for interface endpoints
############################

resource "aws_security_group" "vpc_endpoints" {
  name        = "${local.name_prefix}-vpc-endpoints"
  description = "TLS 443 to AWS interface endpoints from VPC tasks"
  vpc_id      = module.vpc.vpc_id

  tags = {
    FlipSG = "true"
  }
}

resource "aws_security_group_rule" "vpc_endpoints_ingress_from_vpc" {
  type              = "ingress"
  description       = "HTTPS from anywhere in the VPC (ECS tasks + probe)"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  security_group_id = aws_security_group.vpc_endpoints.id
  cidr_blocks       = [var.vpc_cidr]
}

resource "aws_security_group_rule" "vpc_endpoints_egress_all" {
  type              = "egress"
  description       = "Default egress for endpoint ENIs"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.vpc_endpoints.id
  cidr_blocks       = ["0.0.0.0/0"]
}

############################
# Gateway endpoint: S3
############################

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.AWS_REGION}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids
}

############################
# Interface endpoints
############################

locals {
  interface_endpoint_services = toset([
    "ecr.api",
    "ecr.dkr",
    "logs",
    "ssm",
    "ssmmessages",
    "ec2messages",
  ])
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = local.interface_endpoint_services
  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${var.AWS_REGION}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = module.vpc.private_subnets
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
}
