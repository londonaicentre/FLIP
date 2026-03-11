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
  region  = var.AWS_REGION
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

# EC2

module "ec2_security_group" {
  source      = "./modules/secgroup"
  name        = "ec2-security-group"
  vpc_id      = module.flip_vpc.vpc_id
  description = "Security group for FLIP EC2 instance"
  ingress_rules = [
    {
      port        = var.UI_PORT
      description = "FLIP UI"
    },
    {
      port        = var.API_PORT
      description = "FLIP API"
    },
    {
      port        = var.FL_API_PORT
      description = "FLIP FL API"
    },
    {
      port        = var.TRUST_API_PORT
      description = "Trust API"
    },
    {
      port        = var.XNAT_PORT
      description = "XNAT access"
    },
    {
      port        = var.PACS_UI_PORT
      description = "Orthanc PACS UI access"
    },
    {
      port        = 22
      description = "SSH access"
    }
  ]
}

resource "aws_security_group_rule" "fl_server_ingress" {
  type              = "ingress"
  from_port         = 8002
  to_port           = 8002
  protocol          = "tcp"
  cidr_blocks       = ["${module.trust_ec2.public_ip}/32"]
  security_group_id = module.ec2_security_group.security_group.id
  description       = "FL Server from Trust EC2"
}

resource "aws_security_group_rule" "fl_admin_ingress" {
  type              = "ingress"
  from_port         = 8003
  to_port           = 8003
  protocol          = "tcp"
  cidr_blocks       = ["${module.trust_ec2.public_ip}/32"]
  security_group_id = module.ec2_security_group.security_group.id
  description       = "FL Admin Port from Trust EC2"
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
  source                     = "terraform-aws-modules/rds/aws"
  version                    = "~> 6.0"
  identifier                 = "flip-database"
  engine                     = "postgres"
  engine_version             = "13.22"
  auto_minor_version_upgrade = false
  instance_class             = "db.t3.micro"
  allocated_storage          = 20
  username                   = var.POSTGRES_USER
  db_name                    = var.POSTGRES_DB
  db_subnet_group_name       = aws_db_subnet_group.flip_db_subnet_group.name
  vpc_security_group_ids     = [module.rds_security_group.security_group.id]
  backup_retention_period    = 7
  skip_final_snapshot        = true
  family                     = "postgres13"
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
    aes_key               = var.AES_KEY_BASE64
    trust_endpoints       = {
      "Trust_1" = "http://${module.trust_ec2.public_ip}:${var.TRUST_API_PORT}",
      "Trust_2" = "http://${module.trust_ec2.public_ip}:${var.TRUST_API_PORT}"
    }
    trust_ca_cert         = try(file("${path.module}/trust-ca.crt"), "")
  })
}

############################
# EC2
############################

# IAM Role for EC2 instance
module "ec2_role" {
  source                = "terraform-aws-modules/iam/aws//modules/iam-assumable-role"
  version               = "~> 5.0"
  role_name             = "ec2-role"
  create_role           = "true"
  trusted_role_services = ["ec2.amazonaws.com"]
  custom_role_policy_arns = [
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
    "arn:aws:iam::aws:policy/AmazonCognitoPowerUser", # TODO Restrict this policy to only what we need in production
    "arn:aws:iam::aws:policy/AmazonS3FullAccess",     # TODO Restrict this policy to only what we need in production
    "arn:aws:iam::aws:policy/AmazonSESFullAccess",    # TODO Restrict this policy to only what we need in production
    "arn:aws:iam::aws:policy/SecretsManagerReadWrite" # TODO could create a read-only policy instead
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

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "flip_log_group" {
  name              = "/aws/ec2/flip"
  retention_in_days = 7
}

# Key Pair for SSH access
resource "aws_key_pair" "flip_keypair" {
  key_name   = "flip-keypair"
  public_key = file("${var.flip_keypair}.pub")
}

# EC2 Instance
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

resource "aws_instance" "ec2_instance" {
  tags = {
    Name = "Ec2Instance"
  }
  subnet_id                   = module.flip_vpc.public_subnets[0]
  associate_public_ip_address = true
  instance_type               = "t3.small"
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

# Application Load Balancer
module "alb_security_group" {
  source      = "./modules/secgroup"
  name        = "alb-security-group"
  vpc_id      = module.flip_vpc.vpc_id
  description = "Security group for FLIP ALB"
  ingress_rules = [
    {
      port        = var.ALB_HTTPS_PORT
      description = "HTTPS traffic"
    },
    {
      port        = var.API_PORT
      description = "API traffic"
    },
    {
      port        = var.FL_API_PORT
      description = "FL API traffic"
    },
    {
      port        = 8002
      description = "FL Server traffic"
    },
    {
      port        = var.ALB_HTTP_PORT
      description = "HTTP traffic (redirect to HTTPS)"
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
    "https-listener" = {
      port            = var.ALB_HTTPS_PORT
      protocol        = "HTTPS"
      certificate_arn = aws_acm_certificate.flip.arn
      forward = {
        target_group_key = "ec2-instance-ui"
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
    },
    "fl-server-listener" = {
      port     = 8002
      protocol = "HTTP"
      forward = {
        target_group_key = "ec2-instance-fl-server"
      }
    }
  }

  target_groups = {
    ec2-instance-ui = {
      port      = var.UI_PORT
      protocol  = "HTTP"
      target_id = aws_instance.ec2_instance.id
    },
    ec2-instance-api = {
      port      = var.API_PORT
      protocol  = "HTTP"
      target_id = aws_instance.ec2_instance.id
    },
    ec2-instance-fl-api = {
      port      = var.FL_API_PORT
      protocol  = "HTTP"
      target_id = aws_instance.ec2_instance.id
    },
    # TODO FL Server communication should use gRPC protocol
    # TODO When upgrading nvflare to 2.7 or later, note FL Server port consolidation:
    # https://nvflare.readthedocs.io/en/2.7.0/user_guide/admin_guide/configurations/server_port_consolidation.html
    ec2-instance-fl-server = {
      port      = 8002
      protocol  = "HTTP"
      target_id = aws_instance.ec2_instance.id
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

  alias {
    name                   = module.alb.dns_name
    zone_id                = module.alb.zone_id
    evaluate_target_health = true
  }
}

# Listener rule for path-based routing to API
# Routes specific API paths to avoid conflicts with UI routes
resource "aws_lb_listener_rule" "api_path_routing" {
  listener_arn = module.alb.listeners["https-listener"].arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = module.alb.target_groups["ec2-instance-api"].arn
  }

  condition {
    path_pattern {
      values = ["/cohort/*", "/files/*", "/fl/*", "/model/*", "/health"]
    }
  }
}

# Additional listener rule for API documentation paths
resource "aws_lb_listener_rule" "api_docs_routing" {
  listener_arn = module.alb.listeners["https-listener"].arn
  priority     = 101

  action {
    type             = "forward"
    target_group_arn = module.alb.target_groups["ec2-instance-api"].arn
  }

  condition {
    path_pattern {
      values = ["/docs", "/openapi.json", "/redoc", "/prompts/*", "/roles/*"]
    }
  }
}

# Additional listener rule for trust and site API paths
resource "aws_lb_listener_rule" "api_trust_site_routing" {
  listener_arn = module.alb.listeners["https-listener"].arn
  priority     = 102

  action {
    type             = "forward"
    target_group_arn = module.alb.target_groups["ec2-instance-api"].arn
  }

  condition {
    path_pattern {
      values = ["/trust/*", "/site/*"]
    }
  }
}

# Listener rule for user and project API endpoints (priority 99 - higher priority)
# Note: /users and /projects in UI are frontend routes, not API endpoints
# API endpoints for users/projects should use more specific paths or HTTP methods
resource "aws_lb_listener_rule" "api_user_project_routing" {
  listener_arn = module.alb.listeners["https-listener"].arn
  priority     = 99

  action {
    type             = "forward"
    target_group_arn = module.alb.target_groups["ec2-instance-api"].arn
  }

  condition {
    path_pattern {
      values = ["/user/*", "/project/*"]
    }
  }
}

############################
# On-Premises Trust (optional)
# Activated by setting local_trust_public_ip in the env file or via
# TF_VAR_local_trust_public_ip when running `make add-local-trust`.
############################

resource "aws_security_group_rule" "local_trust_fl_server" {
  count             = var.local_trust_public_ip != "" ? 1 : 0
  type              = "ingress"
  from_port         = 8002
  to_port           = 8002
  protocol          = "tcp"
  cidr_blocks       = ["${var.local_trust_public_ip}/32"]
  security_group_id = module.ec2_security_group.security_group.id
  description       = "FL Server from on-prem Trust"
}

resource "aws_security_group_rule" "local_trust_fl_admin" {
  count             = var.local_trust_public_ip != "" ? 1 : 0
  type              = "ingress"
  from_port         = 8003
  to_port           = 8003
  protocol          = "tcp"
  cidr_blocks       = ["${var.local_trust_public_ip}/32"]
  security_group_id = module.ec2_security_group.security_group.id
  description       = "FL Admin from on-prem Trust"
}

# Outputs
output "Keypair" {
  value = var.flip_keypair
}

output "Ec2InstanceId" {
  description = "EC2 Instance ID"
  value       = aws_instance.ec2_instance.id
}

output "Ec2PublicIp" {
  description = "EC2 Instance Public IP"
  value       = aws_instance.ec2_instance.public_ip
}

output "SshCommand" {
  description = "SSH command to connect to the instance"
  value       = "ssh -i ${var.flip_keypair} ubuntu@${aws_instance.ec2_instance.public_ip}"
}

output "TrustEc2InstanceId" {
  description = "Trust EC2 Instance ID"
  value       = module.trust_ec2.instance_id
}

output "TrustEc2PublicIp" {
  description = "Trust EC2 Instance Public IP"
  value       = module.trust_ec2.public_ip
}

output "TrustSshCommand" {
  description = "SSH command to connect to the Trust EC2 instance"
  value       = "ssh -i ${var.flip_keypair} ubuntu@${module.trust_ec2.public_ip}"
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
  value       = aws_cognito_user_pool.flip_user_pool.id
}

output "CognitoAppClientId" {
  description = "Cognito App Client ID"
  value       = aws_cognito_user_pool_client.client.id
}

############################
# SES Email Templates
############################

resource "aws_ses_email_identity" "flip_sender" {
  email = var.SES_VERIFIED_EMAIL
}

resource "aws_ses_template" "flip_access_request" {
  name    = "flip-access-request"
  subject = "Access Request from {{name}} on FLIP"
  html    = file("${path.module}/templates/ses/flip-access-request.html")
  text    = file("${path.module}/templates/ses/flip-access-request.txt")
}

resource "aws_ses_template" "flip_xnat_credentials" {
  name    = "flip-xnat-credentials"
  subject = "Your XNAT credentials for {{trust_name}}"
  html    = file("${path.module}/templates/ses/flip-xnat-credentials.html")
  text    = file("${path.module}/templates/ses/flip-xnat-credentials.txt")
}


###################
# Trust
###################
module "trust_ec2" {
  source = "./modules/trust_ec2"

  name_prefix        = "trust"
  instance_type      = "t3.medium"
  key_name           = aws_key_pair.host_key.key_name
  subnet_id          = element(module.flip_vpc.public_subnets, 0)
  security_group_ids = [module.ec2_security_group.security_group.id]
  FL_API_PORT        = var.FL_API_PORT
  TRUST_API_PORT     = var.TRUST_API_PORT
  XNAT_PORT          = var.XNAT_PORT
  PACS_UI_PORT       = var.PACS_UI_PORT
  # pass the compose file content and env file content from the repo
  create_elastic_ip = true
  # attaches the same ec2-role-profile instance profile to the Trust instance
  iam_instance_profile_name = aws_iam_instance_profile.ec2_profile.name
}

resource "aws_key_pair" "host_key" {
  key_name   = "host-aws"
  public_key = file(var.ec2_public_key_path)
}
