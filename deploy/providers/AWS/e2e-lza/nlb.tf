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

# FL leg, workload-account half: the networking-account edge (internet-facing
# NLB, TCP:8002 passthrough → firewall → TGW) lands here on an internal NLB,
# which fronts the e2e-fl Fargate service (ecs.tf).
#
# TCP passthrough only — the dummy target speaks HTTP, but the proof is TCP
# reachability on :8002, not gRPC semantics. In the real design the stream
# is mTLS gRPC end-to-end from the trust FL client to fl-server.
#
# Unlike the web leg's ALB, an NLB is single-AZ-capable, so this leg applies
# against today's single-AZ LZA prod VPC with no gate.

# Per-subnet detail for the static IP assignment below. Read at plan time
# (the subnets pre-exist), unlike an ENI-discovery data source, which would
# defer to apply and break for_each on the first plan.
data "aws_subnet" "app" {
  for_each = toset(data.aws_subnets.app.ids)
  id       = each.value
}

locals {
  # The FL NLB's private IPs are ASSIGNED via subnet_mapping rather than
  # discovered from its ENIs afterwards: known at plan time, stable by
  # construction, and publishable to the networking account before apply.
  fl_nlb_private_ips = [
    for id in local.app_subnet_ids : cidrhost(data.aws_subnet.app[id].cidr_block, var.fl_nlb_host_num)
  ]
}

module "fl_nlb_security_group" {
  source      = "../modules/secgroup"
  name        = "${local.name_prefix}-fl-nlb-sg"
  vpc_id      = data.aws_vpc.lza_prod.id
  description = "e2e LZA internal FL NLB - TCP 8002 from the networking-account ingress subnets and the in-VPC probe"
  ingress_rules = [
    {
      # VPC CIDR: Phase A probe curls. networking_ingress_cidrs: Phase B —
      # the edge NLB path. Trust VPN CIDRs will join this list later; out of
      # scope for the ingress test.
      port        = local.fl_port
      description = "FL TCP from networking-account edge NLB path (Phase B) and VPC-internal probe (Phase A)"
      cidr_blocks = concat([data.aws_vpc.lza_prod.cidr_block], var.networking_ingress_cidrs)
    }
  ]
}

resource "aws_ec2_tag" "fl_nlb_security_group_flip_sg" {
  resource_id = module.fl_nlb_security_group.security_group.id
  key         = "FlipSG"
  value       = "true"
}

module "fl_nlb" {
  source             = "terraform-aws-modules/alb/aws"
  name               = "${local.name_prefix}-fl-nlb"
  load_balancer_type = "network"
  vpc_id             = data.aws_vpc.lza_prod.id
  internal           = true

  # Static private IP per subnet (see local.fl_nlb_private_ips above).
  subnet_mapping = [
    for id in local.app_subnet_ids : {
      subnet_id            = id
      private_ipv4_address = cidrhost(data.aws_subnet.app[id].cidr_block, var.fl_nlb_host_num)
    }
  ]

  create_security_group      = false
  security_groups            = [module.fl_nlb_security_group.security_group.id]
  enable_deletion_protection = false

  # Standalone Fargate TG below, not the module's target_groups map — see
  # aws_lb_target_group.ecs_fl_server_tcp in ../main.tf for why.
  listeners = {
    "fl-tcp-listener" = {
      port     = local.fl_port
      protocol = "TCP"
      forward = {
        target_group_arn = aws_lb_target_group.fl.arn
      }
    }
  }

  target_groups = {}
}

# Target group for the e2e-fl ECS Fargate service. Registered by the ECS
# service via its load_balancer block (ecs.tf). NLB protocol must be TCP —
# in the real chain the gRPC framing is opaque to the NLB and forwarded
# as-is.
resource "aws_lb_target_group" "fl" {
  name        = "e2e-fl"
  port        = local.fl_port
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = data.aws_vpc.lza_prod.id

  health_check {
    enabled             = true
    protocol            = "TCP"
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 30
  }

  deregistration_delay = 30
}
