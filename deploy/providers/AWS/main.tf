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
  }
}

provider "aws" {
  region = var.AWS_REGION
}

############################
# VPC
############################

data "aws_availability_zones" "available" {}

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

# Central Hub Security Group for EC2 instance

module "ec2_security_group" {
  source      = "./modules/secgroup"
  name        = "ec2-security-group"
  vpc_id      = module.flip_vpc.vpc_id
  description = "Security group for FLIP Central Hub EC2 instance"
  # UI is served from CloudFront/S3 — nothing on EC2 listens on var.UI_PORT (443).
  ingress_rules = [
    {
      port                     = var.API_PORT
      description              = "FLIP API from ALB"
      source_security_group_id = module.alb_security_group.security_group.id
    },
    {
      port                     = var.FL_API_PORT
      description              = "FLIP FL API from ALB"
      source_security_group_id = module.alb_security_group.security_group.id
    }
  ]
}

# Trust Security Group for Trust EC2 instance
# NOTE: Trust API port removed — trusts now poll the hub outbound (no inbound connections needed).
# XNAT and PACS UI ports kept for direct researcher access to imaging tools.

module "trust_security_group" {
  source      = "./modules/secgroup"
  name        = "trust-security-group"
  vpc_id      = module.flip_vpc.vpc_id
  description = "Security group for FLIP Trust EC2 instance (no inbound - access via SSM Session Manager and SSM port forwarding)"

  ingress_rules = []
}

# Only allow FL server traffic that arrives through the NLB, not direct client or VPC access.
resource "aws_security_group_rule" "fl_server_ingress_from_nlb" {
  type                     = "ingress"
  from_port                = var.FL_SERVER_PORT
  to_port                  = var.FL_SERVER_PORT
  protocol                 = "tcp"
  source_security_group_id = module.fl_server_nlb.security_group_id
  security_group_id        = module.ec2_security_group.security_group.id
  description              = "FL Server from NLB security group"
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

  # Set recovery window to allow secret recovery after accidental deletion
  # To permanently delete: remove from state first with: terraform state rm module.flip_api_secret
  recovery_window_in_days = 30

  secret_string = jsonencode({
    aes_key                   = var.AES_KEY_BASE64
    trust_api_key_hashes      = var.TRUST_API_KEY_HASHES
    internal_service_key_hash = var.INTERNAL_SERVICE_KEY_HASH
    internal_service_key      = var.INTERNAL_SERVICE_KEY
  })
}

############################
# EC2
############################

# IAM Role for the Central Hub EC2.
#
# Scoped to exactly what flip-api / fl-server need at runtime, plus the SSM
# and CloudWatch managed policies required by Session Manager and the agent.
# The Trust EC2 uses a separate, narrower role (`trust_ec2_role` below) so
# that Cognito / SES / flip-bucket access is not granted on a host that has
# no business calling those APIs.
module "ec2_role" {
  source                = "terraform-aws-modules/iam/aws//modules/iam-assumable-role"
  version               = "~> 5.0"
  role_name             = "ec2-role"
  create_role           = "true"
  trusted_role_services = ["ec2.amazonaws.com"]
  custom_role_policy_arns = [
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
  ]
  role_requires_mfa = "false"
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "ec2-role-profile"
  role = module.ec2_role.iam_role_name
}

# Add permissions to access secrets
resource "aws_iam_role_policy" "ec2_secret" {
  name = "secret-read"
  role = module.ec2_role.iam_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        module.flip_db.db_instance_master_user_secret_arn,
        module.flip_api_secret.secret_arn
      ]
    }]
  })
}

# Scoped S3 access — limited to FLIP application buckets only.
# - flip_bucket: model files / FL results / FL app destination (flip-api).
# - aicentre_bucket: FL participant kits, fetched via `aws s3 cp` during
#   Ansible provisioning on the Central Hub host.
resource "aws_iam_role_policy" "s3_access" {
  name = "flip-s3-scoped"
  role = module.ec2_role.iam_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:CopyObject",
        "s3:DeleteObject",
        "s3:GetBucketLocation",
        "s3:GetObject",
        "s3:HeadObject",
        "s3:ListBucket",
        "s3:PutObject",
      ]
      Resource = [
        aws_s3_bucket.flip_bucket.arn,
        "${aws_s3_bucket.flip_bucket.arn}/*",
        aws_s3_bucket.aicentre_bucket.arn,
        "${aws_s3_bucket.aicentre_bucket.arn}/*",
      ]
    }]
  })
}

# Scoped Cognito access — limited to FLIP user pool operations used by
# flip-api (see flip_api/utils/cognito_helpers.py and the auth/MFA flows).
resource "aws_iam_role_policy" "cognito_access" {
  name = "flip-cognito-scoped"
  role = module.ec2_role.iam_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "cognito-idp:AdminCreateUser",
        "cognito-idp:AdminDeleteUser",
        "cognito-idp:AdminDisableUser",
        "cognito-idp:AdminEnableUser",
        "cognito-idp:AdminGetUser",
        "cognito-idp:AdminInitiateAuth",
        "cognito-idp:AdminRespondToAuthChallenge",
        "cognito-idp:AdminSetUserPassword",
        "cognito-idp:AdminUserGlobalSignOut",
        "cognito-idp:DescribeUserPool",
        "cognito-idp:DescribeUserPoolClient",
        "cognito-idp:ListUsers",
        "cognito-idp:RevokeToken",
      ]
      Resource = [module.cognito.user_pool_arn]
    }]
  })
}

# Scoped SES access — send-only from the verified sender identity
resource "aws_iam_role_policy" "ses_access" {
  name = "flip-ses-scoped"
  role = module.ec2_role.iam_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ses:SendEmail", "ses:SendRawEmail", "ses:SendTemplatedEmail"]
      Resource = [module.ses.sender_identity_arn]
    }]
  })
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

# CloudWatch Log Groups for EC2 hosts. The CloudWatch agent on each host
# (configured via Ansible — see deploy/providers/AWS/site.yml and the
# templates/cloudwatch-agent*.json.j2 files) ships system + Docker logs
# here. Defining the groups in Terraform pins retention deliberately rather
# than relying on the agent's auto-create default (which never expires).
resource "aws_cloudwatch_log_group" "flip_log_group" {
  name              = "/aws/ec2/flip"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "flip_trust_log_group" {
  name              = "/aws/ec2/flip-trust"
  retention_in_days = 7
}

# Key Pair for SSH access
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
  instance_type               = "t3.medium"
  ami                         = data.aws_ssm_parameter.ubuntu.value
  vpc_security_group_ids      = [module.ec2_security_group.security_group.id]
  iam_instance_profile        = aws_iam_instance_profile.ec2_profile.name
  key_name                    = aws_key_pair.flip_keypair.key_name
  root_block_device {
    volume_size           = 30
    volume_type           = "gp3"
    delete_on_termination = true
  }
}

# AWS-managed prefix list containing CloudFront origin-facing egress IPs.
# Used to restrict ALB ingress to CloudFront only, so the ALB cannot be
# reached directly from the internet — a WAF attached to the distribution
# cannot be bypassed if CloudFront is the only path to the ALB.
data "aws_ec2_managed_prefix_list" "cloudfront_origin_facing" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

# Application Load Balancer
#
# Only HTTPS (443) ingress is configured. The security group denies everything
# else from the internet by default:
# - Port 80 (ALB_HTTP_PORT): CloudFront already redirects viewer HTTP to HTTPS
#   at the edge (default_cache_behavior.viewer_protocol_policy=redirect-to-https)
#   and never dials the origin over HTTP (origin_protocol_policy=https-only).
#   The http-redirect listener still exists as a belt-and-braces fallback but
#   is intentionally unreachable externally.
# - API_PORT / FL_API_PORT: no external consumer. flip-api is reached via
#   CloudFront → ALB /api/* rule on HTTPS:443; the FL API is docker-network-only
#   (see check_status.py — health checks `docker exec` into the container, and
#   NET_ENDPOINTS uses the internal docker hostname).
#
# The 443 rule references the AWS-managed `com.amazonaws.global.cloudfront.origin-facing`
# prefix list. AWS counts an SG rule referencing a managed prefix list against
# the per-SG rule quota using the list's `MaxEntries`, not its current size —
# so a single reference consumes the majority of the default 60-rule quota.
# That's why we can only afford one prefix-list-backed ingress rule here.
module "alb_security_group" {
  source      = "./modules/secgroup"
  name        = "alb-security-group"
  vpc_id      = module.flip_vpc.vpc_id
  description = "Security group for FLIP ALB"
  ingress_rules = [
    {
      port            = var.ALB_HTTPS_PORT
      description     = "HTTPS traffic from CloudFront"
      prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id]
    }
  ]
}

module "alb" {
  source                     = "terraform-aws-modules/alb/aws"
  name                       = "flip-alb"
  vpc_id                     = module.flip_vpc.vpc_id
  subnets                    = module.flip_vpc.public_subnets
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
    },
    "api-listener" = {
      port     = var.API_PORT
      protocol = "HTTP"
      forward = {
        target_group_key = "ec2-instance-api"
      }
    },
    "fl-api-listener" = {
      port     = var.FL_API_PORT
      protocol = "HTTP"
      forward = {
        target_group_key = "ec2-instance-fl-api"
      }
    }
  }

  # UI is served from S3 + CloudFront; no ec2-instance-ui target group.
  target_groups = {
    ec2-instance-api = {
      port      = var.API_PORT
      protocol  = "HTTP"
      target_id = aws_instance.ec2_instance.id

      health_check = {
        enabled  = true
        protocol = "HTTP"
        path     = "/api/health"
        port     = "traffic-port"
        matcher  = "200"
      }
    },
    ec2-instance-fl-api = {
      port      = var.FL_API_PORT
      protocol  = "HTTP"
      target_id = aws_instance.ec2_instance.id
    }
  }
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

  listeners = {
    "fl-server-tcp-listener" = {
      port     = var.FL_SERVER_PORT
      protocol = "TCP"
      forward = {
        target_group_key = "ec2-instance-fl-server-tcp"
      }
    }
  }

  target_groups = {
    ec2-instance-fl-server-tcp = {
      port        = var.FL_SERVER_PORT
      protocol    = "TCP"
      target_type = "instance"
      target_id   = aws_instance.ec2_instance.id

      health_check = {
        enabled             = true
        protocol            = "TCP"
        port                = "traffic-port"
        healthy_threshold   = 3
        unhealthy_threshold = 3
        interval            = 30
      }
    }
  }
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

# Listener rule for path-based routing to the API namespace
resource "aws_lb_listener_rule" "api_routing" {
  listener_arn = module.alb.listeners["https-listener"].arn
  priority     = 98

  action {
    type             = "forward"
    target_group_arn = module.alb.target_groups["ec2-instance-api"].arn
  }

  condition {
    path_pattern {
      values = ["/api", "/api/*"]
    }
  }
}

############################
# On-Premises Trust (optional)
# Activated by setting local_trust_public_ip in the env file or via
# TF_VAR_local_trust_public_ip when running `make add-local-trust`.
############################

# Allow the local (on-prem) trust FL client to reach the FL server via the NLB.
# Without this rule the NLB security group drops the connection before it reaches the EC2.
resource "aws_security_group_rule" "local_trust_fl_server_nlb" {
  count             = var.local_trust_public_ip != "" ? 1 : 0
  type              = "ingress"
  from_port         = var.FL_SERVER_PORT
  to_port           = var.FL_SERVER_PORT
  protocol          = "tcp"
  cidr_blocks       = ["${var.local_trust_public_ip}/32"]
  security_group_id = module.fl_server_nlb.security_group_id
  description       = "FL Server/Admin NLB from on-prem Trust"
}

# Outputs
output "Keypair" {
  value = var.flip_keypair
}

output "Ec2InstanceId" {
  description = "EC2 Instance ID"
  value       = aws_instance.ec2_instance.id
}

output "Ec2PrivateIp" {
  description = "Central Hub EC2 Private IP (private subnet)"
  value       = aws_instance.ec2_instance.private_ip
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
  description = "Trust EC2 Instance ID"
  value       = module.trust_ec2.instance_id
}

output "TrustSsmCommand" {
  description = "SSM Session Manager command to connect to the Trust EC2"
  value       = "aws ssm start-session --target ${module.trust_ec2.instance_id}"
}

output "DbEndpoint" {
  description = "RDS Database Endpoint"
  value       = module.flip_db.db_instance_address
}

output "DbSecretArn" {
  description = "RDS Database Secret ARN"
  value       = module.flip_db.db_instance_master_user_secret_arn
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
