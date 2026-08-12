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

# Security groups for the three ECS Fargate services. Each SG is a
# standalone aws_security_group with standalone aws_security_group_rule
# resources — never inline rules — to prevent AWS provider drift between
# plan and apply cycles.

############################
# flip-api
############################

resource "aws_security_group" "ecs_flip_api" {
  name        = "ecs-flip-api"
  description = "ECS flip-api task - inbound HTTP from ALB"
  vpc_id      = module.flip_vpc.vpc_id

  tags = {
    FlipSG = "true"
  }
}

resource "aws_security_group_rule" "ecs_flip_api_ingress_alb_http" {
  type                     = "ingress"
  description              = "HTTP from ALB"
  from_port                = local.api_container_port
  to_port                  = local.api_container_port
  protocol                 = "tcp"
  security_group_id        = aws_security_group.ecs_flip_api.id
  source_security_group_id = module.alb_security_group.security_group.id
}

resource "aws_security_group_rule" "ecs_flip_api_ingress_vpc_http" {
  type              = "ingress"
  description       = "HTTP from VPC (fl-server callbacks via Service Discovery)"
  from_port         = local.api_container_port
  to_port           = local.api_container_port
  protocol          = "tcp"
  security_group_id = aws_security_group.ecs_flip_api.id
  cidr_blocks       = [var.vpc_cidr]
}

resource "aws_security_group_rule" "ecs_flip_api_egress_all" {
  type              = "egress"
  description       = "Allow all outbound (NAT for AWS API calls, RDS, VPC endpoints)"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.ecs_flip_api.id
  cidr_blocks       = ["0.0.0.0/0"]
}

############################
# fl-api-net-1
############################

resource "aws_security_group" "ecs_fl_api" {
  name        = "ecs-fl-api"
  description = "ECS fl-api-net-1 task - inbound HTTP from VPC"
  vpc_id      = module.flip_vpc.vpc_id

  tags = {
    FlipSG = "true"
  }
}

resource "aws_security_group_rule" "ecs_fl_api_ingress_vpc_http" {
  type              = "ingress"
  description       = "HTTP from VPC (fl-server to fl-api)"
  from_port         = local.api_container_port
  to_port           = local.api_container_port
  protocol          = "tcp"
  security_group_id = aws_security_group.ecs_fl_api.id
  cidr_blocks       = [var.vpc_cidr]
}

resource "aws_security_group_rule" "ecs_fl_api_egress_all" {
  type              = "egress"
  description       = "Allow all outbound"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.ecs_fl_api.id
  cidr_blocks       = ["0.0.0.0/0"]
}

############################
# fl-server-net-1
############################

resource "aws_security_group" "ecs_fl_server" {
  name        = "ecs-fl-server"
  description = "ECS fl-server-net-1 task - inbound gRPC from NLB + HTTP from VPC"
  vpc_id      = module.flip_vpc.vpc_id

  tags = {
    FlipSG = "true"
  }
}

resource "aws_security_group_rule" "ecs_fl_server_ingress_nlb_grpc" {
  type        = "ingress"
  description = "gRPC from NLB (FL client connections)"
  # Backend-dependent container port (Flower: SuperLink Fleet 9092) — the
  # NLB forwards its FL_SERVER_PORT listener here.
  from_port                = local.fl_server_container_port
  to_port                  = local.fl_server_container_port
  protocol                 = "tcp"
  security_group_id        = aws_security_group.ecs_fl_server.id
  source_security_group_id = module.fl_server_nlb.security_group_id
}

# fl-api (the NVFLARE admin client) connects directly to fl-server on the
# admin port (same 8002 as the gRPC client port; NVFLARE multiplexes admin
# and client RPC over a single HTTP/2 stream). It does NOT go through the
# NLB - that path is reserved for off-VPC FL clients on trusts. Without
# this rule fl-api startup hangs in `try_connect` until the 20s timeout.
resource "aws_security_group_rule" "ecs_fl_server_ingress_fl_api_admin" {
  type                     = "ingress"
  description              = "NVFLARE admin from fl-api task (direct, not via NLB)"
  from_port                = 8002
  to_port                  = 8002
  protocol                 = "tcp"
  security_group_id        = aws_security_group.ecs_fl_server.id
  source_security_group_id = aws_security_group.ecs_fl_api.id
}

# Flower control plane (fl_backend=flower only): the fl-api submits runs to
# the SuperLink's Exec API (9093) and polls its health server (9097) over
# Cloud Map — direct task-to-task, never via the NLB. The register-supernode-
# keys one-shot (ecs_flower.tf) runs with the fl-api SG so these same rules
# cover its 9093 calls.
resource "aws_security_group_rule" "ecs_fl_server_ingress_fl_api_flower_exec" {
  count                    = var.fl_backend == "flower" ? 1 : 0
  type                     = "ingress"
  description              = "Flower SuperLink Exec API from fl-api task"
  from_port                = local.flower_superlink_exec_port
  to_port                  = local.flower_superlink_exec_port
  protocol                 = "tcp"
  security_group_id        = aws_security_group.ecs_fl_server.id
  source_security_group_id = aws_security_group.ecs_fl_api.id
}

resource "aws_security_group_rule" "ecs_fl_server_ingress_fl_api_flower_health" {
  count                    = var.fl_backend == "flower" ? 1 : 0
  type                     = "ingress"
  description              = "Flower SuperLink health server from fl-api task"
  from_port                = local.flower_superlink_health_port
  to_port                  = local.flower_superlink_health_port
  protocol                 = "tcp"
  security_group_id        = aws_security_group.ecs_fl_server.id
  source_security_group_id = aws_security_group.ecs_fl_api.id
}

resource "aws_security_group_rule" "ecs_fl_server_ingress_vpc_http" {
  type              = "ingress"
  description       = "HTTP from VPC (internal API calls)"
  from_port         = local.api_container_port
  to_port           = local.api_container_port
  protocol          = "tcp"
  security_group_id = aws_security_group.ecs_fl_server.id
  cidr_blocks       = [var.vpc_cidr]
}

resource "aws_security_group_rule" "ecs_fl_server_egress_all" {
  type              = "egress"
  description       = "Allow all outbound (NAT for AWS API calls, Service Discovery)"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.ecs_fl_server.id
  cidr_blocks       = ["0.0.0.0/0"]
}
