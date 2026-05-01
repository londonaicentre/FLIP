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

# VPC endpoints let Fargate tasks pull images, read SSM parameters, fetch
# secrets, and write logs without traversing the NAT gateway. Required because
# Fargate tasks in private subnets need outbound access to AWS APIs at task
# launch — without these, image pulls and secret reads silently hang.
#
# Gated behind enable_ecs_endpoints: creating 5 interface endpoints costs
# ~$73/month in idle ENI hourly charges. Until PR 2 deploys ECS services
# that consume them, existing EC2 traffic uses the NAT gateway just fine.
# The S3 gateway endpoint is always created (no hourly charge).

############################
# Security group for interface endpoints
############################

resource "aws_security_group" "vpc_endpoints" {
  count       = var.enable_ecs_endpoints ? 1 : 0
  name        = "vpc-endpoints"
  description = "TLS 443 to AWS interface endpoints from VPC tasks"
  vpc_id      = module.flip_vpc.vpc_id
}

resource "aws_security_group_rule" "vpc_endpoints_ingress_from_vpc" {
  count             = var.enable_ecs_endpoints ? 1 : 0
  type              = "ingress"
  description       = "HTTPS from anywhere in the VPC (ECS tasks)"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  security_group_id = aws_security_group.vpc_endpoints[0].id
  cidr_blocks       = [var.vpc_cidr]
}

resource "aws_security_group_rule" "vpc_endpoints_egress_all" {
  count             = var.enable_ecs_endpoints ? 1 : 0
  type              = "egress"
  description       = "Default egress for endpoint ENIs"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.vpc_endpoints[0].id
  cidr_blocks       = ["0.0.0.0/0"]
}

############################
# Gateway endpoint: S3
############################

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.flip_vpc.vpc_id
  service_name      = "com.amazonaws.${var.AWS_REGION}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.flip_vpc.private_route_table_ids
}

############################
# Interface endpoints
############################

locals {
  interface_endpoint_services = toset([
    "secretsmanager",
    "ssm",
    "logs",
    "ecr.api",
    "ecr.dkr",
  ])
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = var.enable_ecs_endpoints ? local.interface_endpoint_services : toset([])
  vpc_id              = module.flip_vpc.vpc_id
  service_name        = "com.amazonaws.${var.AWS_REGION}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = module.flip_vpc.private_subnets
  security_group_ids  = [aws_security_group.vpc_endpoints[0].id]
  private_dns_enabled = true
}
