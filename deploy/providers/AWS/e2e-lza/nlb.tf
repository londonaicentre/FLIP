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

locals {
  # Also used to locate the NLB's ENIs by description below — keep in sync
  # with module.fl_nlb's name.
  fl_nlb_name = "${local.name_prefix}-fl-nlb"
}

module "fl_nlb_security_group" {
  source      = "../modules/secgroup"
  name        = "${local.name_prefix}-fl-nlb-sg"
  vpc_id      = module.vpc.vpc_id
  description = "e2e LZA internal FL NLB - TCP 8002 from the networking-account ingress subnets and the in-VPC probe"
  ingress_rules = [
    {
      # VPC CIDR: Phase A probe curls. networking_ingress_cidrs: Phase B —
      # the edge NLB path. Trust VPN CIDRs will join this list later; out of
      # scope for the ingress test.
      port        = local.fl_port
      description = "FL TCP from networking-account edge NLB path (Phase B) and VPC-internal probe (Phase A)"
      cidr_blocks = concat([var.vpc_cidr], var.networking_ingress_cidrs)
    }
  ]
}

resource "aws_ec2_tag" "fl_nlb_security_group_flip_sg" {
  resource_id = module.fl_nlb_security_group.security_group.id
  key         = "FlipSG"
  value       = "true"
}

module "fl_nlb" {
  source                     = "terraform-aws-modules/alb/aws"
  name                       = local.fl_nlb_name
  load_balancer_type         = "network"
  vpc_id                     = module.vpc.vpc_id
  internal                   = true
  subnets                    = module.vpc.private_subnets
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
  vpc_id      = module.vpc.vpc_id

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

############################
# NLB static private IPs — the FL-leg handoff value
############################
# The networking account registers these as IP targets on its internet-facing
# edge NLB's :8002 listener. NLB ENI addresses are stable for the load
# balancer's lifetime, which is why the FL leg needs no DNS-sync machinery —
# unlike the ALB, whose IPs rotate (see parameter_store.tf).

data "aws_network_interfaces" "fl_nlb" {
  filter {
    name = "description"
    # Wildcard-safe form: ENI descriptions are "ELB net/<name>/<lb-id>" and
    # the lb-id part is only known after creation.
    values = ["ELB net/${local.fl_nlb_name}/*"]
  }

  # No attribute reference ties this read to the NLB (the filter is a static
  # string), so without an explicit depends_on it would be read at plan time
  # and find nothing on the first apply.
  depends_on = [module.fl_nlb]
}

data "aws_network_interface" "fl_nlb" {
  for_each = toset(data.aws_network_interfaces.fl_nlb.ids)
  id       = each.value
}

locals {
  fl_nlb_private_ips = sort([for eni in data.aws_network_interface.fl_nlb : eni.private_ip])
}
