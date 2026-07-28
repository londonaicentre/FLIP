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

# Web leg, workload-account half: the networking-account edge (CloudFront +
# WAF → internal NLB, TCP:443 passthrough → firewall → TGW) lands here on an
# internal ALB, which fronts the e2e-web Fargate service (ecs.tf).
#
# GATED behind var.enable_web_leg: an ALB requires subnets in >= 2 AZs and
# the LZA prod VPC template is single-AZ today (README open question 2).
# Everything web-leg-specific in this stack carries the same gate so the FL
# leg can apply and be tested first.
#
# Plain HTTP:80 for the connectivity proof — in the target design TLS
# terminates at this ALB (:443 + ACM cert). That swap is a documented
# follow-up in README.md. Note the CloudFront-VPC-origin shortcut used by
# legacy prod is SCP-denied in the LZA (lza PR #36), so this ALB-behind-TGW
# chain is the only sanctioned web path.

module "web_alb_security_group" {
  count       = var.enable_web_leg ? 1 : 0
  source      = "../modules/secgroup"
  name        = "${local.name_prefix}-web-alb-sg"
  vpc_id      = data.aws_vpc.lza_prod.id
  description = "e2e LZA internal web ALB - HTTP from the networking-account ingress subnets and the in-VPC probe"
  ingress_rules = [
    {
      # The VPC CIDR is load-bearing for Phase A: the runbook's probe curls
      # hit this ALB from inside the VPC before the networking side exists.
      # networking_ingress_cidrs is [] until Phase B (README) — CIDR rules
      # only, cross-account SG references do not work over a TGW attachment.
      port        = local.web_port
      description = "HTTP from networking-account ingress (Phase B) and VPC-internal probe (Phase A)"
      cidr_blocks = concat([data.aws_vpc.lza_prod.cidr_block], var.networking_ingress_cidrs)
    }
  ]
}

# Tag the secgroup module SGs for drift detection — same externally-applied
# pattern as ../main.tf (the module has no tags variable).
resource "aws_ec2_tag" "web_alb_security_group_flip_sg" {
  count       = var.enable_web_leg ? 1 : 0
  resource_id = module.web_alb_security_group[0].security_group.id
  key         = "FlipSG"
  value       = "true"
}

module "web_alb" {
  count                      = var.enable_web_leg ? 1 : 0
  source                     = "terraform-aws-modules/alb/aws"
  name                       = "${local.name_prefix}-web-alb"
  vpc_id                     = data.aws_vpc.lza_prod.id
  internal                   = true
  subnets                    = local.app_subnet_ids
  create_security_group      = false
  security_groups            = [module.web_alb_security_group[0].security_group.id]
  enable_deletion_protection = false

  # Listener forwards directly to the ECS Fargate TG defined as a standalone
  # aws_lb_target_group below. We bypass the module's target_groups map
  # because that map only supports target_type=instance bound to an EC2 id;
  # Fargate awsvpc requires target_type=ip with no pre-registered targets
  # (see aws_lb_target_group.ecs_fl_server_tcp in ../main.tf).
  listeners = {
    "web-http-listener" = {
      port     = local.web_port
      protocol = "HTTP"
      forward = {
        target_group_arn = aws_lb_target_group.web[0].arn
      }
    }
  }

  target_groups = {}
}

# Target group for the e2e-web ECS Fargate service. Registered by the ECS
# service via its load_balancer block (ecs.tf) — we never attach targets
# here. target_type=ip is required for awsvpc Fargate tasks.
resource "aws_lb_target_group" "web" {
  count       = var.enable_web_leg ? 1 : 0
  name        = "e2e-web"
  port        = local.web_container_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = data.aws_vpc.lza_prod.id

  health_check {
    enabled  = true
    protocol = "HTTP"
    path     = "/"
    port     = "traffic-port"
    matcher  = "200"
  }

  deregistration_delay = 30
}
