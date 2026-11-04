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
# Security Groups
############################

# Central Hub SSM bastion security group. Session Manager is outbound-only;
# the group is retained as the source for the RDS PostgreSQL ingress rule.

module "ec2_security_group" {
  source        = "./modules/secgroup"
  name          = "ec2-security-group"
  vpc_id        = local.vpc_id
  description   = "Security group for the FLIP Central Hub SSM bastion (no inbound access)"
  ingress_rules = []
  tags          = { FlipSG = "true" }
}

# The FlipSG drift-detection tag on the secgroup-module groups used to be set
# by standalone `aws_ec2_tag` resources while the group itself declared no
# tags, so every apply stripped what the other had restored (#1392). The tag is
# now the module's `tags` argument. `destroy = false` forgets the old resources
# rather than deleting the live tag on the way out. Safe to drop these blocks
# once every state file (stag, prod, both LZA estates) has been applied.
removed {
  from = aws_ec2_tag.ec2_security_group_flip_sg
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_ec2_tag.trust_security_group_flip_sg
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_ec2_tag.rds_security_group_flip_sg
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_ec2_tag.alb_security_group_flip_sg
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_ec2_tag.fl_internal_nlb_security_group_flip_sg
  lifecycle {
    destroy = false
  }
}

# RDS
# TODO: In Production we need to activate delete protection to the RDS instances
module "rds_security_group" {
  source      = "./modules/secgroup"
  name        = "rds-security-group"
  vpc_id      = local.vpc_id
  description = "Security group for FLIP RDS instance"
  ingress_rules = [
    {
      port                     = 5432
      description              = "PostgreSQL from EC2"
      source_security_group_id = module.ec2_security_group.security_group.id
    }
  ]
  block_all_outbound = true
  tags               = { FlipSG = "true" }
}

# flip-api reaches Postgres only through RDS Proxy (rds_proxy.tf), never
# directly — so RDS ingress comes from the proxy SG (rds_proxy.tf), not the
# flip-api task SG.

############################
# RDS PostgreSQL Database
############################

resource "aws_db_subnet_group" "flip_db_subnet_group" {
  name = "flip-db-subnet-group"
  # Data subnets: on the LZA network these are the fully-isolated (local-routes
  # only) subnets — RDS never initiates outbound traffic, and the proxy /
  # bastion reach it over intra-VPC routing (see network_lza.tf).
  subnet_ids = local.data_subnet_ids
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
  # Explicit, not the module default (FLIP#1576); kms_key_id would force replacement, see the test.
  storage_encrypted = true
  family            = "postgres${split(".", var.postgres_version)[0]}"
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
  source      = "terraform-aws-modules/iam/aws//modules/iam-assumable-role"
  version     = "~> 5.0"
  role_name   = "ec2-role"
  create_role = "true"
  # FLIP#962: the CI apply role may only create a boundary-carrying role.
  role_permissions_boundary_arn = local.iam_permissions_boundary_arn
  trusted_role_services         = ["ec2.amazonaws.com"]
  custom_role_policy_arns = [
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
  ]
  role_requires_mfa = "false"
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "ec2-role-profile"
  role = module.ec2_role.iam_role_name
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
  subnet_id                   = local.app_subnet_ids[0]
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

  # IMDSv2 only (FLIP#1058): session tokens close the classic SSRF →
  # instance-credential-theft read. Nothing on this host speaks IMDSv1, and the
  # default hop limit of 1 stands — the bastion runs no containers.
  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  # The SSM parameter moves with every Canonical release, and ami forces
  # replacement: without this, each new image replaces the host on the next
  # apply, unattended ones in CI included, and site.yml is not re-run for it.
  # Replacing on purpose (the generation marker above, or -replace) still
  # launches from the current image.
  lifecycle {
    ignore_changes = [ami]
  }
}

# The ALB went with the self-contained mode (FLIP#1479). Its security group, and the flip-api
# ingress rule that names it (ecs_sg.tf), still exist on both LZA estates: unused, kept for a
# separate cleanup.
module "alb_security_group" {
  source        = "./modules/secgroup"
  name          = "alb-security-group"
  vpc_id        = local.vpc_id
  description   = "Security group for FLIP ALB"
  ingress_rules = []
  tags          = { FlipSG = "true" }
}

# Skipped when the account has no hosted zone (var.manage_dns = false — the
# zone-less first LZA bring-up, FLIP#749): the lookup would hard-fail there.
data "aws_route53_zone" "subdomain" {
  count = var.manage_dns ? 1 : 0
  name  = var.flip_alb_subdomain
}

# Target group for the fl-server-net-1 ECS Fargate service. Registered by
# the ECS service via the load_balancer block in ecs_services.tf - we never
# attach instance/IP targets here. target_type=ip is required for awsvpc
# Fargate tasks. NLB protocol must be TCP - HTTP/2 gRPC framing is opaque
# to the NLB and forwarded as-is.
# The internal NLB (fl_ingress_lza.tf) forwards its FL listener here. The name
# keeps the `-lza` suffix the live states hold (a name change is ForceNew).
resource "aws_lb_target_group" "ecs_fl_server_tcp" {
  # Container port per backend (FLARE: FL_SERVER_PORT; Flower: the fleet proxy's
  # 9092, FLIP#1517 — see local.fl_server_container_port in ecs_tasks.tf). The
  # NLB LISTENER stays on var.FL_SERVER_PORT for both. Port is ForceNew, so
  # switching backend replaces this TG; the name is keyed by backend and
  # create_before_destroy set so the replacement can stand up while the old
  # TG is still attached to the listener (a same-name replace deadlocks).
  name        = "ecs-fl-server${var.fl_backend == "flower" ? "-flwr" : ""}-lza"
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

  # Match flip-api TG (30s) - long deregistration on rolling deploys would
  # block training-round handshakes against a draining task.
  deregistration_delay = 30
}

# State migrations (FLIP#749): the LZA states were first applied with their own
# `_lza` copies of the two target groups, folded into the resources above and
# below. Safe to remove once every live state file has been migrated.
moved {
  from = aws_lb_target_group.ecs_fl_server_tcp_lza[0]
  to   = aws_lb_target_group.ecs_fl_server_tcp
}

moved {
  from = aws_lb_target_group.ecs_flip_api_lza[0]
  to   = aws_lb_target_group.ecs_flip_api
}

# Target group for the flip-api ECS Fargate service. Registered by the ECS
# service itself via the load_balancer block in ecs_services.tf - we never
# attach instance/IP targets here from terraform. target_type=ip is required
# for awsvpc Fargate tasks (each task gets an ENI; the IP is what ECS
# registers, not an instance id).
# The internal NLB's web listener (fl_ingress_lza.tf) forwards here over TCP —
# NLB target groups are TCP, but the health check stays HTTP on the API's own
# liveness route. Name and protocol are the ones the live states hold (both ForceNew).
resource "aws_lb_target_group" "ecs_flip_api" {
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

  # ECS rolling deploys briefly need both old + new tasks present; a long
  # deregistration delay would stretch every cutover. 30s is enough for
  # in-flight requests to drain without holding rollouts hostage.
  deregistration_delay = 30
}

output "Ec2InstanceId" {
  description = "Central Hub SSM bastion instance ID"
  value       = aws_instance.ec2_instance.id
}

output "SsmCommand" {
  description = "SSM Session Manager command to connect to the Central Hub"
  value       = "aws ssm start-session --target ${aws_instance.ec2_instance.id}"
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
  description = "FL server DNS endpoint (NLB pass-through; on LZA the name resolves to the networking account's edge NLB, which relays to the internal NLB in fl_ingress_lza.tf)"
  value       = var.flip_nlb_subdomain
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
  from = aws_ses_template.flip_xnat_added_to_project
  to   = module.ses.aws_ses_template.flip_xnat_added_to_project
}
