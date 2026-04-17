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
  ingress_rules = [
    {
      port                     = var.UI_PORT
      description              = "FLIP UI from ALB"
      source_security_group_id = module.alb_security_group.security_group.id
    },
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
  source                     = "terraform-aws-modules/rds/aws"
  version                    = "~> 6.0"
  identifier                 = "flip-database"
  engine                     = "postgres"
  engine_version             = var.postgres_version
  auto_minor_version_upgrade = false
  instance_class             = "db.t3.micro"
  allocated_storage          = 20
  username                   = var.POSTGRES_USER
  db_name                    = var.POSTGRES_DB
  db_subnet_group_name       = aws_db_subnet_group.flip_db_subnet_group.name
  vpc_security_group_ids     = [module.rds_security_group.security_group.id]
  backup_retention_period    = 7
  skip_final_snapshot        = true
  family                     = "postgres${split(".", var.postgres_version)[0]}"
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

    # Referenced by ECS task definitions via valueFrom with JSON key syntax.
    db_password = var.db_password
    github_pat  = var.github_pat
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

  alias {
    name                   = module.alb.dns_name
    zone_id                = module.alb.zone_id
    evaluate_target_health = true
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
# ECS Tasks Security Group
# Attached to all Fargate tasks running in private subnets.
# Ingress is allowed only from the ALB security group on the API container port
# and from within the VPC CIDR for internal service-to-service calls.
############################

resource "aws_security_group" "ecs_tasks" {
  name        = "ecs-tasks-sg"
  vpc_id      = module.flip_vpc.vpc_id
  description = "Security group for all FLIP ECS Fargate tasks"

  # Allow ALB → flip-api on container port 8000
  ingress {
    description     = "flip-api from ALB"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [module.alb_security_group.security_group.id]
  }

  # Allow internal VPC traffic so ECS tasks can call each other
  # (e.g. trust-api → imaging-api, trust-api → data-access-api)
  ingress {
    description = "Internal VPC service-to-service"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # Allow all outbound (to reach RDS, S3, Secrets Manager, SSM, Cognito endpoints)
  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "ecs-tasks-sg"
  }
}

# Allow ECS tasks to reach the FLIP RDS instance (PostgreSQL)
resource "aws_security_group_rule" "rds_ingress_from_ecs" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.ecs_tasks.id
  security_group_id        = module.rds_security_group.security_group.id
  description              = "PostgreSQL from ECS tasks"
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
  description       = "FL Server NLB from on-prem Trust"
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
  value       = aws_cognito_user_pool.flip_user_pool.id
}

output "CognitoAppClientId" {
  description = "Cognito App Client ID"
  value       = aws_cognito_user_pool_client.client.id
}

output "FlServerEndpoint" {
  description = "FL server DNS endpoint (NLB pass-through)"
  value       = var.flip_nlb_subdomain
}

output "FlServerRawNlbDns" {
  description = "Raw AWS NLB DNS name for FL server debugging"
  value       = module.fl_server_nlb.dns_name
}

output "EcsClusterName" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.flip.name
}

output "EcsClusterArn" {
  description = "ECS cluster ARN"
  value       = aws_ecs_cluster.flip.arn
}

output "EcsFlipApiServiceName" {
  description = "ECS service name for flip-api"
  value       = aws_ecs_service.flip_api.name
}

output "EcsTrustApiServiceName" {
  description = "ECS service name for trust-api"
  value       = aws_ecs_service.trust_api.name
}

output "EcsImagingApiServiceName" {
  description = "ECS service name for imaging-api"
  value       = aws_ecs_service.imaging_api.name
}

output "EcsDataAccessApiServiceName" {
  description = "ECS service name for data-access-api"
  value       = aws_ecs_service.data_access_api.name
}

output "EcsFlipApiTargetGroupArn" {
  description = "ARN of the ALB target group for the ECS flip-api service"
  value       = aws_lb_target_group.ecs_flip_api.arn
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

resource "aws_ses_template" "flip_xnat_added_to_project" {
  name    = "flip-xnat-added-to-project"
  subject = "You have been added to a project at {{trust_name}}"
  html    = file("${path.module}/templates/ses/flip-xnat-added-to-project.html")
  text    = file("${path.module}/templates/ses/flip-xnat-added-to-project.txt")
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

  # attaches the same ec2-role-profile instance profile to the Trust instance
  iam_instance_profile_name = aws_iam_instance_profile.ec2_profile.name
}

resource "aws_key_pair" "host_key" {
  key_name   = "host-aws"
  public_key = file(var.ec2_public_key_path)
}
