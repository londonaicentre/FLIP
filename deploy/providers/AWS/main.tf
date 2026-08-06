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

terraform {
  required_version = ">= 1.13.1"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region = var.AWS_REGION
}

provider "null" {
}

############################
# VPC
############################

data "aws_availability_zones" "available" {}

# Cross-stack: aicentre-iac's network_account_flip module reads
# /flip/networking/vpc_id and /flip/networking/private_subnet_ids from
# this account's SSM (see parameter_store.tf) to back the cross-account
# TGW VPC attachment. If you recreate or rename this VPC, plan against
# aicentre-iac immediately afterwards.
module "flip_vpc" {
  source               = "terraform-aws-modules/vpc/aws"
  version              = "~> 6.0"
  name                 = "flip-vpc"
  azs                  = slice(data.aws_availability_zones.available.names, 0, var.max_azs)
  cidr                 = var.vpc_cidr
  public_subnets       = var.public_subnets
  private_subnets      = var.private_subnets
  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true
}

############################
# Security Groups
############################

# Central Hub SSM bastion security group. Session Manager is outbound-only;
# the group is retained as the source for the RDS PostgreSQL ingress rule.

module "ec2_security_group" {
  source        = "./modules/secgroup"
  name          = "ec2-security-group"
  vpc_id        = module.flip_vpc.vpc_id
  description   = "Security group for the FLIP Central Hub SSM bastion (no inbound access)"
  ingress_rules = []
}

# Tag the secgroup module SGs for drift detection.
# Remove these aws_security_group_tags resources if the secgroup module
# ever adds a `tags` variable — until then, we tag externally.
resource "aws_ec2_tag" "ec2_security_group_flip_sg" {
  resource_id = module.ec2_security_group.security_group.id
  key         = "FlipSG"
  value       = "true"
}

# Trust Security Group for Trust EC2 instance
# NOTE: Trust API port removed — trusts now poll the hub outbound (no inbound connections needed).
# XNAT and PACS UI ports kept for direct researcher access to imaging tools.

# Egress allowlist for the trust EC2 (#876, GHSA-8465). Security groups can't match on hostname,
# so CDN-fronted / dynamic-IP destinations (GHCR, Docker Hub, the Central Hub's CloudFront domain,
# Hugging Face mock-data seeding, OS/package install sources) are scoped to 0.0.0.0/0 on port 443
# as the practical floor for an SG-only design — accepted as a permanent, documented limitation,
# not a gap slated for a domain-aware firewall follow-up. ssmmessages/ec2messages and CloudWatch
# monitoring have no VPC endpoint provisioned at all (unlike ssm/secretsmanager/logs, gated by
# var.enable_ecs_endpoints below) and get the same public-CIDR floor rather than three more
# interface endpoints. See deploy/providers/AWS/README.md for the full rationale per destination.
locals {
  # Every entry carries the full attribute set (unused selectors explicitly null) so Terraform's
  # object-type unification across this heterogeneous list — and across the two branches of the
  # enable_ecs_endpoints ternary below — doesn't choke on "inconsistent conditional result types".
  trust_egress_rule_defaults = {
    protocol                 = "tcp"
    cidr_blocks              = null
    source_security_group_id = null
    prefix_list_ids          = null
  }

  trust_egress_rules = concat(
    [
      for r in [
        {
          port        = 443
          cidr_blocks = ["0.0.0.0/0"]
          description = "Central Hub API (CloudFront) - trust-api polling"
        },
        {
          port        = 443
          cidr_blocks = ["0.0.0.0/0"]
          description = "GHCR - fl-client/trust-api/imaging-api/data-access-api/orthanc image pulls"
        },
        {
          port        = 443
          cidr_blocks = ["0.0.0.0/0"]
          description = "Docker Hub - grafana loki/alloy/grafana image pulls"
        },
        {
          port        = 443
          cidr_blocks = ["0.0.0.0/0"]
          description = "Hugging Face - trust mock OMOP/Orthanc data seeding (make seed-trust-data)"
        },
        {
          port        = 443
          cidr_blocks = ["0.0.0.0/0"]
          description = "download.docker.com - Docker Engine install/upgrade (geerlingguy.docker role)"
        },
        {
          port        = 443
          cidr_blocks = ["0.0.0.0/0"]
          description = "awscli.amazonaws.com - one-time AWS CLI v2 install"
        },
        {
          port        = 80
          cidr_blocks = ["0.0.0.0/0"]
          description = "Ubuntu apt mirrors (HTTP) - package install/upgrade"
        },
        {
          port        = 443
          cidr_blocks = ["0.0.0.0/0"]
          description = "Ubuntu apt mirrors and esm.ubuntu.com (HTTPS) - package install/upgrade"
        },
        {
          port        = 443
          cidr_blocks = ["0.0.0.0/0"]
          description = "CloudWatch metrics (monitoring) - no VPC endpoint provisioned"
        },
        {
          port        = 443
          cidr_blocks = ["0.0.0.0/0"]
          description = "SSM Session Manager data channel (ssmmessages) - no VPC endpoint provisioned"
        },
        {
          port        = 443
          cidr_blocks = ["0.0.0.0/0"]
          description = "SSM Session Manager EC2 channel (ec2messages) - no VPC endpoint provisioned"
        },
        {
          port            = 443
          prefix_list_ids = [data.aws_ec2_managed_prefix_list.s3.id]
          description     = "S3 (AI Centre FL kit sync) - AWS-managed prefix list, not a raw CIDR"
        },
        {
          port                     = var.FL_SERVER_PORT
          source_security_group_id = module.fl_server_nlb.security_group_id
          description              = "FL server NLB - FL training traffic"
        },
        {
          port        = 53
          protocol    = "tcp"
          cidr_blocks = ["${cidrhost(var.vpc_cidr, 2)}/32"]
          description = "VPC DNS resolver (TCP fallback for large responses)"
        },
        {
          port        = 53
          protocol    = "udp"
          cidr_blocks = ["${cidrhost(var.vpc_cidr, 2)}/32"]
          description = "VPC DNS resolver"
        },
      ] : merge(local.trust_egress_rule_defaults, r)
    ],
    # ssm and logs interface endpoints only exist when var.enable_ecs_endpoints is true (the
    # default) - scope tightly to the endpoint SG when they exist, otherwise fall back to the
    # same public-CIDR floor already accepted above for the endpoints that never exist.
    var.enable_ecs_endpoints ? [
      for r in [
        {
          port                     = 443
          source_security_group_id = aws_security_group.vpc_endpoints[0].id
          description              = "SSM control plane (ssm) - via VPC interface endpoint"
        },
        {
          port                     = 443
          source_security_group_id = aws_security_group.vpc_endpoints[0].id
          description              = "CloudWatch Logs (logs) - via VPC interface endpoint"
        },
      ] : merge(local.trust_egress_rule_defaults, r)
      ] : [
      for r in [
        {
          port        = 443
          cidr_blocks = ["0.0.0.0/0"]
          description = "SSM control plane (ssm) - no VPC endpoint provisioned (enable_ecs_endpoints=false)"
        },
        {
          port        = 443
          cidr_blocks = ["0.0.0.0/0"]
          description = "CloudWatch Logs (logs) - no VPC endpoint provisioned (enable_ecs_endpoints=false)"
        },
      ] : merge(local.trust_egress_rule_defaults, r)
    ]
  )
}

module "trust_security_group" {
  source      = "./modules/secgroup"
  name        = "trust-security-group"
  vpc_id      = module.flip_vpc.vpc_id
  description = "Security group for FLIP Trust EC2 instance (no inbound - access via SSM Session Manager and SSM port forwarding)"

  ingress_rules      = []
  block_all_outbound = true
  egress_rules       = local.trust_egress_rules
}

resource "aws_ec2_tag" "trust_security_group_flip_sg" {
  resource_id = module.trust_security_group.security_group.id
  key         = "FlipSG"
  value       = "true"
}

# RDS
# TODO: In Production we need to activate delete protection to the RDS instances
module "rds_security_group" {
  source      = "./modules/secgroup"
  name        = "rds-security-group"
  vpc_id      = module.flip_vpc.vpc_id
  description = "Security group for FLIP RDS instance"
  ingress_rules = [
    {
      port                     = 5432
      description              = "PostgreSQL from EC2"
      source_security_group_id = module.ec2_security_group.security_group.id
    }
  ]
  block_all_outbound = true
}

resource "aws_ec2_tag" "rds_security_group_flip_sg" {
  resource_id = module.rds_security_group.security_group.id
  key         = "FlipSG"
  value       = "true"
}

# flip-api reaches Postgres only through RDS Proxy (rds_proxy.tf), never
# directly — so RDS ingress comes from the proxy SG (rds_proxy.tf), not the
# flip-api task SG.

############################
# RDS PostgreSQL Database
############################

resource "aws_db_subnet_group" "flip_db_subnet_group" {
  name       = "flip-db-subnet-group"
  subnet_ids = module.flip_vpc.private_subnets
}

module "flip_db" {
  source                           = "terraform-aws-modules/rds/aws"
  version                          = "~> 6.0"
  identifier                       = "flip-database"
  engine                           = "postgres"
  engine_version                   = var.postgres_version
  auto_minor_version_upgrade       = true
  instance_class                   = "db.t3.micro"
  allocated_storage                = 20
  username                         = var.POSTGRES_USER
  db_name                          = var.POSTGRES_DB
  db_subnet_group_name             = aws_db_subnet_group.flip_db_subnet_group.name
  vpc_security_group_ids           = [module.rds_security_group.security_group.id]
  backup_retention_period          = 7
  skip_final_snapshot              = var.environment != "prod"
  deletion_protection              = var.environment == "prod"
  final_snapshot_identifier_prefix = "flip-database-final"
  family                           = "postgres${split(".", var.postgres_version)[0]}"
}

############################
# Secrets
############################

module "flip_api_secret" {
  source      = "terraform-aws-modules/secrets-manager/aws"
  version     = "2.0.0"
  name        = "FLIP_API"
  description = "FLIP_API"
  kms_key_id  = aws_kms_key.flip_app_key.arn

  # Set recovery window to allow secret recovery after accidental deletion
  # To permanently delete: remove from state first with: terraform state rm module.flip_api_secret
  recovery_window_in_days = 30

  secret_string = jsonencode({
    aes_key                   = var.AES_KEY_BASE64
    internal_service_key_hash = var.INTERNAL_SERVICE_KEY_HASH
    internal_service_key      = var.INTERNAL_SERVICE_KEY
  })
}

############################
# EC2
############################

# IAM role for the Central Hub SSM bastion. Application workloads run on ECS
# Fargate and use the task roles in iam_ecs.tf; the bastion needs no access to
# application secrets, buckets, Cognito, SES, or CloudWatch Logs.
module "ec2_role" {
  source                = "terraform-aws-modules/iam/aws//modules/iam-assumable-role"
  version               = "~> 5.0"
  role_name             = "ec2-role"
  create_role           = "true"
  trusted_role_services = ["ec2.amazonaws.com"]
  custom_role_policy_arns = [
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
  ]
  role_requires_mfa = "false"
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "ec2-role-profile"
  role = module.ec2_role.iam_role_name
}

# IAM Role for the Trust EC2.
#
# The Trust host runs trust-api, imaging-api, data-access-api, fl-client, XNAT,
# Orthanc and the OMOP DB — none of those services use boto3. The only AWS
# call from the Trust host is `aws s3 sync` against the AI Centre bucket
# during Ansible provisioning to fetch the FL participant kit (see the
# NVFLARE/Flower kit-download tasks in deploy/providers/AWS/site.yml).
# Cognito, SES and the FLIP application bucket are deliberately *not*
# granted here.
#
# Future directions — both would let us drop the trust_ec2_s3 policy below
# and leave the Trust role with only SSM + CloudWatch:
#
#   1. Presigned URLs. flip-api already mints short-lived presigned URLs for
#      user-facing S3 ops (see flip_api/utils/s3_client.py::get_presigned_url
#      and ::get_put_presigned_url). Ansible could pull the kit over plain
#      HTTPS via ansible.builtin.get_url against a URL minted by an
#      authenticated trust-api → flip-api call — no AWS credentials on the
#      Trust host at all.
#
#   2. Out-of-band kit delivery. The on-prem (hybrid) trust playbook
#      (deploy/providers/local/site_local_trust.yml) already works this way:
#      the operator stages the kit on their workstation via
#      `make add-local-trust` and rsyncs it onto the host. Applying the same
#      pattern to AWS-hosted trusts would remove S3 entirely from the Trust
#      role's blast radius.
module "trust_ec2_role" {
  source                = "terraform-aws-modules/iam/aws//modules/iam-assumable-role"
  version               = "~> 5.0"
  role_name             = "trust-ec2-role"
  create_role           = "true"
  trusted_role_services = ["ec2.amazonaws.com"]
  custom_role_policy_arns = [
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
  ]
  role_requires_mfa = "false"
}

resource "aws_iam_instance_profile" "trust_ec2_profile" {
  name = "trust-ec2-role-profile"
  role = module.trust_ec2_role.iam_role_name
}

# Read-only access to the AI Centre bucket for FL participant-kit downloads.
resource "aws_iam_role_policy" "trust_ec2_s3" {
  name = "s3-aicentre-read"
  role = module.trust_ec2_role.iam_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
        ]
        Resource = [aws_s3_bucket.aicentre_bucket.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.aicentre_bucket.arn}/*"]
      },
    ]
  })
}

# The Trust EC2 still runs application containers and ships system + Docker
# logs through its CloudWatch agent. The Central Hub bastion has no app logs.
resource "aws_cloudwatch_log_group" "flip_trust_log_group" {
  name              = "/aws/ec2/flip-trust"
  retention_in_days = 7
}

# Retain the keypair for SSH-over-SSM (`ssh flip`) and Ansible. No inbound SSH
# rule is required: the Session Manager ProxyCommand carries the SSH stream.
resource "aws_key_pair" "flip_keypair" {
  key_name   = "flip-keypair"
  public_key = file(pathexpand("${var.flip_keypair}.pub"))
}

# EC2 Instance
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

resource "aws_instance" "ec2_instance" {
  tags = {
    Name = "Ec2Instance"
  }
  subnet_id                   = module.flip_vpc.private_subnets[0]
  associate_public_ip_address = false
  instance_type               = "t3.micro"
  ami                         = data.aws_ssm_parameter.ubuntu.value
  # Changing the generation marker replaces the bastion. Generation 1 forces
  # the legacy 30 GB application host to be recreated because EBS volumes
  # cannot be shrunk in place to the minimal 10 GB root volume.
  user_data                   = <<-EOT
    #!/bin/bash
    set -eu
    echo "1" > /etc/flip-bastion-generation
  EOT
  user_data_replace_on_change = true
  vpc_security_group_ids      = [module.ec2_security_group.security_group.id]
  iam_instance_profile        = aws_iam_instance_profile.ec2_profile.name
  key_name                    = aws_key_pair.flip_keypair.key_name
  root_block_device {
    volume_size           = 10
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true
  }
}

# Application Load Balancer
#
# Internal (no public IP). CloudFront reaches it via aws_cloudfront_vpc_origin
# (see cloudfront.tf) over an AWS-managed ENI inside this VPC — the ALB has
# no internet exposure and the prefix-list-based ingress rule of the old
# public-ALB design is no longer needed.
#
# Ingress on 443 is added separately as `aws_security_group_rule.alb_ingress_https_from_cloudfront`
# in cloudfront.tf, with source = the CloudFront-VPCOrigins-Service-SG that AWS
# creates after the VPC origin is provisioned (AWS docs Option 2 in
# https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html).
# We learned the hard way that a vpc_cidr-based rule does NOT permit VPC-origin
# traffic — AWS scopes VPC-origin SG checks to the service-managed SG (or the
# CloudFront managed prefix list), not the ENI's source IP. The rule lives
# outside this module so the chain (ALB SG → ALB → VPC origin → service-SG
# data source → SG rule) doesn't form a cycle.
# The HTTP listener still exists as a redirect-to-HTTPS belt-and-braces but
# the SG denies inbound 80 by default.
module "alb_security_group" {
  source        = "./modules/secgroup"
  name          = "alb-security-group"
  vpc_id        = module.flip_vpc.vpc_id
  description   = "Security group for FLIP ALB"
  ingress_rules = []
}

resource "aws_ec2_tag" "alb_security_group_flip_sg" {
  resource_id = module.alb_security_group.security_group.id
  key         = "FlipSG"
  value       = "true"
}

module "alb" {
  source                     = "terraform-aws-modules/alb/aws"
  name                       = "flip-alb"
  vpc_id                     = module.flip_vpc.vpc_id
  internal                   = true
  subnets                    = module.flip_vpc.private_subnets
  security_groups            = [module.alb_security_group.security_group.id]
  enable_deletion_protection = false

  listeners = {
    # HTTPS default action: return 404. CloudFront is the canonical front door
    # for user traffic; anything reaching the ALB default action (e.g. direct
    # ALB DNS probes) gets rejected here. The /api/* listener rule below
    # forwards API requests to the API target group for both CloudFront's
    # /api/* behaviour and any direct trust access.
    "https-listener" = {
      port            = var.ALB_HTTPS_PORT
      protocol        = "HTTPS"
      certificate_arn = aws_acm_certificate.flip.arn
      ssl_policy      = "ELBSecurityPolicy-TLS13-1-3-2021-06"
      fixed_response = {
        content_type = "text/plain"
        message_body = "Not Found"
        status_code  = "404"
      }
    },
    "http-redirect" = {
      port     = var.ALB_HTTP_PORT
      protocol = "HTTP"
      redirect = {
        port        = tostring(var.ALB_HTTPS_PORT)
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }

  # No EC2-instance target groups — the EC2 host no longer runs application
  # containers (they run on ECS Fargate). Legacy `api-listener` and
  # `fl-api-listener` listeners were removed in PR #452 review; the ALB
  # only routes the https-listener /api/* path to the ECS target group.
  target_groups = {}
}

# Network Load Balancer for FL server TCP/TLS pass-through
module "fl_server_nlb" {
  source                     = "terraform-aws-modules/alb/aws"
  name                       = "flip-fl-server-nlb"
  load_balancer_type         = "network"
  vpc_id                     = module.flip_vpc.vpc_id
  subnets                    = module.flip_vpc.public_subnets
  enable_deletion_protection = false
  create_security_group      = true
  security_group_tags        = { FlipSG = "true" }

  # NLB only accepts trusted client sources - allow-list only the trusted client egress IPs
  # TODO explore 'internal' NLB plus private connectivity instead of an internet-facing NLB
  security_group_ingress_rules = {
    fl_server_ingress = {
      description = "Allow inbound FL server traffic only from trusted FL client IP"
      ip_protocol = "tcp"
      from_port   = tostring(var.FL_SERVER_PORT)
      to_port     = tostring(var.FL_SERVER_PORT)
      cidr_ipv4   = "${module.flip_vpc.nat_public_ips[0]}/32"
    }
  }

  security_group_egress_rules = {
    fl_server_egress = {
      description = "Allow NLB traffic and health checks to FL server targets"
      ip_protocol = "tcp"
      from_port   = tostring(var.FL_SERVER_PORT)
      to_port     = tostring(var.FL_SERVER_PORT)
      cidr_ipv4   = var.vpc_cidr
    }
  }

  # Listener forwards directly to the ECS Fargate TG defined as a standalone
  # aws_lb_target_group below. We bypass the module's target_groups map
  # because that map only supports target_type=instance bound to an EC2 id;
  # Fargate awsvpc requires target_type=ip with no pre-registered targets.
  listeners = {
    "fl-server-tcp-listener" = {
      port     = var.FL_SERVER_PORT
      protocol = "TCP"
      forward = {
        target_group_arn = aws_lb_target_group.ecs_fl_server_tcp.arn
      }
    }
  }

  # No module-managed target groups - the ECS TG is the standalone resource
  # below. Leaving the legacy `ec2-instance-fl-server-tcp` definition would
  # keep the EC2 instance attached as an unhealthy target and contradict the
  # post-cutover state.
  target_groups = {}
}

data "aws_route53_zone" "subdomain" {
  name = var.flip_alb_subdomain
}

resource "aws_route53_record" "alb" {
  zone_id = data.aws_route53_zone.subdomain.zone_id
  name    = var.flip_alb_subdomain
  type    = "A"

  # Canonical user-facing URL — aliased to the CloudFront distribution.
  # (Resource is still named "alb" for TF-state backwards compatibility; a
  # rename would recreate the record. The alias target is now CloudFront.)
  alias {
    name                   = aws_cloudfront_distribution.flip_ui.domain_name
    zone_id                = aws_cloudfront_distribution.flip_ui.hosted_zone_id
    evaluate_target_health = false
  }
}

# Target group for the fl-server-net-1 ECS Fargate service. Registered by
# the ECS service via the load_balancer block in ecs_services.tf - we never
# attach instance/IP targets here. target_type=ip is required for awsvpc
# Fargate tasks. NLB protocol must be TCP - HTTP/2 gRPC framing is opaque
# to the NLB and forwarded as-is.
resource "aws_lb_target_group" "ecs_fl_server_tcp" {
  name        = "ecs-fl-server-tcp"
  port        = var.FL_SERVER_PORT
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = module.flip_vpc.vpc_id

  health_check {
    enabled             = true
    protocol            = "TCP"
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 30
  }

  # Match flip-api TG (30s) - long deregistration on rolling deploys would
  # block training-round handshakes against a draining task.
  deregistration_delay = 30
}

resource "aws_route53_record" "fl_server_nlb" {
  zone_id = data.aws_route53_zone.subdomain.zone_id
  name    = var.flip_nlb_subdomain
  type    = "A"

  alias {
    name                   = module.fl_server_nlb.dns_name
    zone_id                = module.fl_server_nlb.zone_id
    evaluate_target_health = true
  }
}

# Target group for the flip-api ECS Fargate service. Registered by the ECS
# service itself via the load_balancer block in ecs_services.tf - we never
# attach instance/IP targets here from terraform. target_type=ip is required
# for awsvpc Fargate tasks (each task gets an ENI; the IP is what ECS
# registers, not an instance id).
resource "aws_lb_target_group" "ecs_flip_api" {
  name        = "ecs-flip-api"
  port        = local.api_container_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = module.flip_vpc.vpc_id

  health_check {
    enabled  = true
    protocol = "HTTP"
    path     = "/api/health"
    port     = "traffic-port"
    matcher  = "200"
  }

  # ECS rolling deploys briefly need both old + new tasks present; a long
  # deregistration delay would stretch every cutover. 30s is enough for
  # in-flight requests to drain without holding rollouts hostage.
  deregistration_delay = 30
}

# Listener rule for path-based routing to the API namespace. Forwards /api/*
# to the ECS Fargate target group above. The legacy `ec2-instance-api`
# target group on the EC2 host is kept in module.alb for state continuity
# but no longer wired to a listener rule.
resource "aws_lb_listener_rule" "api_routing" {
  listener_arn = module.alb.listeners["https-listener"].arn
  priority     = 98

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ecs_flip_api.arn
  }

  condition {
    path_pattern {
      values = ["/api", "/api/*"]
    }
  }
}

############################
# On-Premises Trust (optional)
# Driven by var.local_trust_public_ips — set via LOCAL_TRUST_PUBLIC_IPS in the
# env file. One ingress rule is created per IP; `make allow-local-trust-nlb`
# applies them. Because the IPs are real config (not a transient -target var),
# a normal `terraform apply` reconciles these rules without drift.
############################

# Allow on-prem trust FL clients to reach the FL server via the NLB.
# Without this rule the NLB security group drops the connection before it reaches the EC2.
resource "aws_security_group_rule" "local_trust_fl_server_nlb" {
  for_each          = toset(var.local_trust_public_ips)
  type              = "ingress"
  from_port         = var.FL_SERVER_PORT
  to_port           = var.FL_SERVER_PORT
  protocol          = "tcp"
  cidr_blocks       = ["${each.value}/32"]
  security_group_id = module.fl_server_nlb.security_group_id
  description       = "FL Server/Admin NLB from on-prem Trust (${each.value})"
}

############################
# K8s Trust (optional)
# Driven by var.k8s_trust_public_ips — set via K8S_TRUST_PUBLIC_IPS in the env
# file. One ingress rule per IP, keyed by the IP itself (for_each), so the set
# is reconciled by a normal `terraform apply` and re-adding an existing IP is a
# no-op — fixing the InvalidPermission.Duplicate from the old -target/count path
# (#596). The legacy scalar K8S_TRUST_IP is merged in for back-compat.
############################

# Allow K8s-deployed trust FL clients to reach the FL server via the NLB.
# Same pattern as the on-prem trust rule above.
resource "aws_security_group_rule" "k8s_trust_fl_server_nlb" {
  # The deprecated scalar is marked sensitive for backwards compatibility,
  # but an address used as a resource key is necessarily disclosed in state.
  for_each = toset(concat(
    var.k8s_trust_public_ips,
    nonsensitive(var.K8S_TRUST_IP) != "" ? [nonsensitive(var.K8S_TRUST_IP)] : []
  ))
  type              = "ingress"
  from_port         = var.FL_SERVER_PORT
  to_port           = var.FL_SERVER_PORT
  protocol          = "tcp"
  cidr_blocks       = ["${each.value}/32"]
  security_group_id = module.fl_server_nlb.security_group_id
  description       = "FL Server/Admin NLB from K8s Trust (${each.value})"
}

output "Ec2InstanceId" {
  description = "Central Hub SSM bastion instance ID"
  value       = aws_instance.ec2_instance.id
}

output "SsmCommand" {
  description = "SSM Session Manager command to connect to the Central Hub"
  value       = "aws ssm start-session --target ${aws_instance.ec2_instance.id}"
}

output "NatGatewayPublicIp" {
  description = "NAT Gateway public IP (Central Hub outbound traffic source)"
  value       = module.flip_vpc.nat_public_ips[0]
}

output "TrustEc2InstanceId" {
  description = "Trust EC2 Instance ID (empty string on a hub-only deployment, deploy_trust_ec2=false)"
  value       = try(module.trust_ec2[0].instance_id, "")
}

output "TrustSsmCommand" {
  description = "SSM Session Manager command to connect to the Trust EC2 (empty on a hub-only deployment)"
  value       = var.deploy_trust_ec2 ? "aws ssm start-session --target ${try(module.trust_ec2[0].instance_id, "")}" : ""
}

output "DbEndpoint" {
  description = "RDS Database Endpoint"
  value       = module.flip_db.db_instance_address
}

output "DbSecretArn" {
  description = "RDS Database Secret ARN"
  value       = module.flip_db.db_instance_master_user_secret_arn
}

output "DbProxyEndpoint" {
  description = "RDS Proxy endpoint (flip-api connects here with IAM auth)"
  value       = aws_db_proxy.flip_db.endpoint
}

output "CognitoUserPoolId" {
  description = "Cognito User Pool ID"
  value       = module.cognito.user_pool_id
}

output "CognitoAppClientId" {
  description = "Cognito App Client ID"
  value       = module.cognito.app_client_id
}

output "FlServerEndpoint" {
  description = "FL server DNS endpoint (NLB pass-through)"
  value       = var.flip_nlb_subdomain
}

output "FlServerRawNlbDns" {
  description = "Raw AWS NLB DNS name for FL server debugging"
  value       = module.fl_server_nlb.dns_name
}

############################
# SES Email Templates
############################
#
# Resource definitions now live in ./modules/ses. The existing four
# resources are migrated automatically by the `moved` blocks below on the
# next plan/apply — no manual `terraform state mv` step is required.

module "ses" {
  source = "./modules/ses"

  sender_email  = var.SES_VERIFIED_EMAIL
  templates_dir = "${path.module}/templates/ses"
  # template_name_prefix left empty so prod keeps its existing SES template
  # names (flip-access-request etc.) and this refactor is a pure state-mv.
}

# State migration: SES resources used to live at the root of this stack and now
# live inside module.ses. See the matching `moved` block in services.tf for the
# rationale. Safe to remove once every live state file has been migrated.
moved {
  from = aws_ses_email_identity.flip_sender
  to   = module.ses.aws_ses_email_identity.flip_sender
}

moved {
  from = aws_ses_template.flip_access_request
  to   = module.ses.aws_ses_template.flip_access_request
}

moved {
  from = aws_ses_template.flip_xnat_credentials
  to   = module.ses.aws_ses_template.flip_xnat_credentials
}

moved {
  from = aws_ses_template.flip_xnat_added_to_project
  to   = module.ses.aws_ses_template.flip_xnat_added_to_project
}


###################
# Trust
###################
module "trust_ec2" {
  # Optional: a hub-only deployment (var.deploy_trust_ec2=false, `make
  # full-deploy-hub-only`) provisions no cloud trust host — every trust then
  # runs on-prem and joins via register-trusts + allow-local-trust-nlb.
  count  = var.deploy_trust_ec2 ? 1 : 0
  source = "./modules/trust_ec2"

  name_prefix   = "trust"
  instance_type = "t3.xlarge"
  key_name      = aws_key_pair.host_key.key_name
  subnet_id     = element(module.flip_vpc.private_subnets, 0)

  # use the trust SG, not the central EC2 SG
  security_group_ids = [module.trust_security_group.security_group.id]

  # Trust EC2 uses its own narrower instance profile (SSM + CloudWatch +
  # read-only S3 on the AI Centre bucket). It does not get Central Hub
  # permissions like Cognito, SES or the FLIP application bucket.
  iam_instance_profile_name = aws_iam_instance_profile.trust_ec2_profile.name
}

resource "aws_key_pair" "host_key" {
  key_name   = "host-aws"
  public_key = file(var.ec2_public_key_path)
}
