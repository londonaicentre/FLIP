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

# LZA-only ingress plumbing (FLIP#749 WP3), the workload-account half of the
# two-tier design proven end-to-end by the e2e harness (FLIP#829/#830), from
# which this file is ported:
#
#   FL:  trust FL client → internet-facing edge NLB :{FL_SERVER_PORT}
#        (networking account) → central firewall → TGW → the INTERNAL NLB
#        below → fl-server ECS task. The legacy internet-facing
#        module.fl_server_nlb is impossible in-account (no IGW + VPC BPA) and
#        stays gated off; this internal NLB exists as the STATIC-IP anchor —
#        its per-subnet IPs are assigned (not discovered) via subnet_mapping,
#        so the networking account registers them once, with no sync
#        machinery.
#   Web: CloudFront + WAF → relay NLB (networking account) → firewall → TGW
#        → the SAME internal NLB below on :{ALB_HTTPS_PORT} → flip-api ECS task.
#        The ALB (module.alb) is gated off on LZA: ALB IPs rotate, which forced
#        the networking account to run a target-sync Lambda per environment;
#        the NLB's assigned IPs do not, so the relay registers them once —
#        the FL leg's contract, now shared by both legs. Legacy prod/stag keep
#        the ALB unchanged.
#
# Everything in this file is inert on legacy prod/stag (count/create-gated on
# var.lza_managed_network, list arguments emptied) — the legacy plan is
# unchanged.

# Per-subnet detail for the static IP assignment below. Read at plan time
# (the subnets pre-exist), unlike an ENI-discovery data source, which would
# defer to apply and break for_each on the first plan.
data "aws_subnet" "lza_app" {
  for_each = var.lza_managed_network ? toset(data.aws_subnets.lza_app[0].ids) : toset([])
  id       = each.value
}

# The prod VPC's TGW attachment — read to filter the PUBLISHED FL NLB IPs to
# AZs whose return path exists (AWS drops TGW traffic in AZs where the
# attachment has no ENI). Since lza#45 the attachment spans both AZs, so this
# publishes everything today; the filter keeps a future third AZ from being
# registered before its attachment subnet lands.
data "aws_ec2_transit_gateway_vpc_attachment" "lza" {
  count = var.lza_managed_network ? 1 : 0

  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.lza[0].id]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

data "aws_subnet" "lza_tgw_attachment" {
  for_each = var.lza_managed_network ? toset(data.aws_ec2_transit_gateway_vpc_attachment.lza[0].subnet_ids) : toset([])
  id       = each.value
}

locals {
  # The FL NLB's private IPs are ASSIGNED via subnet_mapping rather than
  # discovered from its ENIs afterwards: known at plan time, stable by
  # construction, and publishable to the networking account before apply.
  lza_fl_nlb_private_ips = var.lza_managed_network ? [
    for id in local.app_subnet_ids : cidrhost(data.aws_subnet.lza_app[id].cidr_block, var.lza_fl_nlb_host_num)
  ] : []

  lza_tgw_attachment_azs = var.lza_managed_network ? distinct([
    for s in values(data.aws_subnet.lza_tgw_attachment) : s.availability_zone
  ]) : []

  lza_fl_nlb_published_ips = var.lza_managed_network ? [
    for id in local.app_subnet_ids :
    cidrhost(data.aws_subnet.lza_app[id].cidr_block, var.lza_fl_nlb_host_num)
    if contains(local.lza_tgw_attachment_azs, data.aws_subnet.lza_app[id].availability_zone)
  ] : []
}

module "fl_internal_nlb_security_group" {
  count       = var.lza_managed_network ? 1 : 0
  source      = "./modules/secgroup"
  name        = "flip-fl-internal-nlb-sg"
  vpc_id      = local.vpc_id
  description = "Internal FL NLB - FL TCP from the networking-account edge path and in-VPC callers"
  ingress_rules = [
    {
      # VPC CIDR: in-VPC verification (probe curls, fl-api). The networking
      # ingress CIDRs are the edge-NLB → firewall → TGW path. Trust VPN
      # CIDRs join this list when the VPN lands.
      port        = var.FL_SERVER_PORT
      description = "FL TCP from the networking-account edge NLB path and VPC-internal callers"
      cidr_blocks = concat([local.vpc_cidr_block], var.networking_ingress_cidrs)
    },
    {
      # Web leg: the networking-account relay NLB (CloudFront VPC origin →
      # relay → firewall → TGW) dials the internal NLB's web listener. Same
      # sources as the FL rule; the VPC CIDR keeps in-VPC probe curls working.
      port        = var.ALB_HTTPS_PORT
      description = "Web from the networking-account relay NLB path and VPC-internal callers"
      cidr_blocks = concat([local.vpc_cidr_block], var.networking_ingress_cidrs)
    }
  ]
}

resource "aws_ec2_tag" "fl_internal_nlb_security_group_flip_sg" {
  count       = var.lza_managed_network ? 1 : 0
  resource_id = module.fl_internal_nlb_security_group[0].security_group.id
  key         = "FlipSG"
  value       = "true"
}

module "fl_server_internal_nlb" {
  source  = "terraform-aws-modules/alb/aws"
  version = "~> 10.0"
  # LZA counterpart of the legacy module.fl_server_nlb above — created on
  # exactly the opposite gate, so precisely one FL NLB exists per environment.
  create             = var.lza_managed_network
  name               = "flip-fl-internal-nlb"
  load_balancer_type = "network"
  vpc_id             = local.vpc_id
  internal           = true

  # Static private IP per subnet (see local.lza_fl_nlb_private_ips above).
  subnet_mapping = var.lza_managed_network ? [
    for id in local.app_subnet_ids : {
      subnet_id            = id
      private_ipv4_address = cidrhost(data.aws_subnet.lza_app[id].cidr_block, var.lza_fl_nlb_host_num)
    }
  ] : []

  create_security_group      = false
  security_groups            = var.lza_managed_network ? [module.fl_internal_nlb_security_group[0].security_group.id] : []
  enable_deletion_protection = false

  # Standalone Fargate TG below, not the module's target_groups map — same
  # rationale as aws_lb_target_group.ecs_fl_server_tcp in main.tf.
  listeners = {
    "fl-server-tcp-listener" = {
      port     = var.FL_SERVER_PORT
      protocol = "TCP"
      forward = {
        target_group_arn = var.lza_managed_network ? aws_lb_target_group.ecs_fl_server_tcp_lza[0].arn : null
      }
    }

    # Web leg (FLIP#749): CloudFront's relay dials this listener; the ALB is
    # gated off on LZA. Protocol mirrors the ALB's zone-less gating — an
    # ISSUED cert is impossible without a hosted zone (var.manage_dns=false),
    # so the listener is plain TCP until the zone lands, then TLS on the
    # DNS-validated cert. Same port either way so the relay never changes.
    # Flipping to TLS is a cross-repo step: the relay is TCP passthrough, so the TLS client becomes CloudFront's VPC
    # origin in the networking account (aicentre-lza-iac ingress_web.tf), which must switch to https-only with an
    # origin host matching this cert's SAN at the same time.
    "web-listener" = {
      port            = var.ALB_HTTPS_PORT
      protocol        = var.manage_dns ? "TLS" : "TCP"
      certificate_arn = var.manage_dns ? aws_acm_certificate.flip[0].arn : null
      ssl_policy      = var.manage_dns ? "ELBSecurityPolicy-TLS13-1-3-2021-06" : null
      forward = {
        target_group_arn = var.lza_managed_network ? aws_lb_target_group.ecs_flip_api_lza[0].arn : null
      }
    }
  }

  target_groups = {}
}

# LZA counterpart of aws_lb_target_group.ecs_fl_server_tcp (main.tf) —
# identical semantics: backend-keyed name (port is ForceNew, see the comment
# there), container port per backend, registered by the ECS service's
# load_balancer block in ecs_services.tf, never by Terraform.
resource "aws_lb_target_group" "ecs_fl_server_tcp_lza" {
  count       = var.lza_managed_network ? 1 : 0
  name        = var.fl_backend == "flower" ? "ecs-fl-server-flwr-lza" : "ecs-fl-server-lza"
  port        = local.fl_server_container_port
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = local.vpc_id

  lifecycle {
    create_before_destroy = true
  }

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

# LZA web leg counterpart of aws_lb_target_group.ecs_flip_api (main.tf): the
# internal NLB forwards its web listener here. NLB target groups are TCP, but
# the health check is HTTP on the API's own liveness route so an unhealthy
# task is pulled exactly as the ALB did. Registered by the ECS service's
# load_balancer block (ecs_services.tf), never by Terraform.
resource "aws_lb_target_group" "ecs_flip_api_lza" {
  count       = var.lza_managed_network ? 1 : 0
  name        = "ecs-flip-api-lza"
  port        = local.api_container_port
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = local.vpc_id

  lifecycle {
    create_before_destroy = true
  }

  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = "/api/health"
    port                = "traffic-port"
    matcher             = "200"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 30
  }

  # Same drain as the ALB TG: long enough for in-flight requests, short
  # enough not to stretch every ECS rollout.
  deregistration_delay = 30
}

# SSM handoff parameters — the contract with the networking account's edge
# configuration (aicentre-lza-iac), replacing the e2e harness's
# /flip-e2e/networking/ prefix with the real one.
resource "aws_ssm_parameter" "lza_fl_nlb_private_ips" {
  # checkov:skip=CKV2_AWS_34:non-secret networking value read CROSS-ACCOUNT by the networking account's edge stack (aicentre-lza-iac) — an AWS-managed CMK cannot be decrypted from another account
  count       = var.lza_managed_network ? 1 : 0
  name        = "/flip/networking/fl_nlb_private_ips"
  description = "Internal NLB static private IPs (comma-separated, assigned via subnet_mapping so they are stable by construction; TGW-reachable AZs only) - registered ONCE as IP targets on the networking account's edge NLB FL listener AND its web relay target group"
  type        = "StringList"
  value       = join(",", local.lza_fl_nlb_published_ips)
}

resource "aws_ssm_parameter" "lza_fl_port" {
  # checkov:skip=CKV2_AWS_34:non-secret networking value read CROSS-ACCOUNT by the networking account's edge stack (aicentre-lza-iac) — an AWS-managed CMK cannot be decrypted from another account
  count       = var.lza_managed_network ? 1 : 0
  name        = "/flip/networking/fl_port"
  description = "Workload-side FL ingress port (internal NLB listener) - consumed by the networking account's edge configuration"
  type        = "String"
  value       = tostring(var.FL_SERVER_PORT)
}

resource "aws_ssm_parameter" "lza_web_nlb_dns_name" {
  # checkov:skip=CKV2_AWS_34:non-secret networking value read CROSS-ACCOUNT by the networking account's edge stack (aicentre-lza-iac) — an AWS-managed CMK cannot be decrypted from another account
  count       = var.lza_managed_network ? 1 : 0
  name        = "/flip/networking/web_nlb_dns_name"
  description = "Internal NLB DNS name - informational (in-VPC verification, e.g. curl from a task); the networking-account edge does not consume it - it targets the static IPs in fl_nlb_private_ips"
  type        = "String"
  value       = module.fl_server_internal_nlb.dns_name
}

resource "aws_ssm_parameter" "lza_web_port" {
  # checkov:skip=CKV2_AWS_34:non-secret networking value read CROSS-ACCOUNT by the networking account's edge stack (aicentre-lza-iac) — an AWS-managed CMK cannot be decrypted from another account
  count       = var.lza_managed_network ? 1 : 0
  name        = "/flip/networking/web_port"
  description = "Workload-side web ingress port (the internal NLB's web listener; plain TCP on the zone-less bring-up, TLS once manage_dns is true) - the relay NLB's target port on the networking side"
  type        = "String"
  value       = tostring(var.ALB_HTTPS_PORT)
}

# Task-side admission for the internal NLB: the legacy
# ecs_fl_server_ingress_nlb_grpc rule (ecs_sg.tf) references the legacy NLB's
# SG and is gated off on LZA, which left the fl-server task admitting nothing
# on its FL port -- the NLB's health checks parked the target at unhealthy and
# ECS churned replacements forever.
resource "aws_security_group_rule" "ecs_fl_server_ingress_internal_nlb" {
  count       = var.lza_managed_network ? 1 : 0
  type        = "ingress"
  description = "gRPC from the internal FL NLB (edge-relayed FL clients + health checks)"
  # Backend-dependent container port (Flower: SuperLink Fleet 9092) -- the
  # NLB forwards its FL_SERVER_PORT listener here.
  from_port                = local.fl_server_container_port
  to_port                  = local.fl_server_container_port
  protocol                 = "tcp"
  source_security_group_id = module.fl_internal_nlb_security_group[0].security_group.id
  security_group_id        = aws_security_group.ecs_fl_server.id
}

# Same admission for flip-api: the internal NLB's web listener (relayed
# CloudFront traffic + its HTTP health checks) reaches the task on the API
# port. Without it the TG parks the task unhealthy exactly as the fl-server
# rule above describes.
resource "aws_security_group_rule" "ecs_flip_api_ingress_internal_nlb" {
  count                    = var.lza_managed_network ? 1 : 0
  type                     = "ingress"
  description              = "HTTP from the internal NLB web listener (edge-relayed CloudFront traffic + health checks)"
  from_port                = local.api_container_port
  to_port                  = local.api_container_port
  protocol                 = "tcp"
  source_security_group_id = module.fl_internal_nlb_security_group[0].security_group.id
  security_group_id        = aws_security_group.ecs_flip_api.id
}

# The NVFLARE admin kit (provisioned from net-1_project_prod.yml) targets the
# bare host `fl-server-net-1` — a SAN on the server cert alongside the public
# FQDN. Legacy resolves it through the flip.local DHCP search domain
# (dhcp.tf), which the LZA-managed VPC cannot carry, and Fargate's awsvpc
# network mode rejects extraHosts — so publish the bare name as a
# single-label private hosted zone whose apex A records are the internal FL
# NLB's deterministic static IPs. A bare-name lookup exhausts the VPC search
# domains and then matches this zone's apex, landing on the NLB → fl-server.
resource "aws_route53_zone" "fl_server_bare_name" {
  count   = var.lza_managed_network ? 1 : 0
  name    = "fl-server-net-1"
  comment = "LZA: resolve the NVFLARE admin kit's bare fl-server host inside the workload VPC (FLIP#749)"

  vpc {
    vpc_id = local.vpc_id
  }
}

resource "aws_route53_record" "fl_server_bare_name_apex" {
  count   = var.lza_managed_network ? 1 : 0
  zone_id = aws_route53_zone.fl_server_bare_name[0].zone_id
  name    = "fl-server-net-1"
  type    = "A"
  ttl     = 60
  records = local.lza_fl_nlb_private_ips
}
