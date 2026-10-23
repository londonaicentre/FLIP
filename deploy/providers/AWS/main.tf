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

# Trust Security Group for Trust EC2 instance
# NOTE: Trust API port removed — trusts now poll the hub outbound (no inbound connections needed).
# XNAT and PACS UI ports kept for direct researcher access to imaging tools.

# Egress allowlist for the trust EC2 (#876, GHSA-8465). Security groups can't match on hostname, so
# every CDN-fronted / dynamic-IP destination (GHCR, Docker Hub, the Central Hub's CloudFront domain,
# Hugging Face mock-data seeding, PyPI for that seeding's `uv run`, OS/package install sources, and the
# AWS services with no VPC endpoint — ssmmessages, ec2messages, monitoring) collapses into a single
# 0.0.0.0/0 rule per port. That is the practical floor for an SG-only design — accepted as a
# permanent, documented limitation, not a gap slated for a domain-aware firewall follow-up.
#
# ONE RULE PER AWS TUPLE. An EC2 security-group rule is identified by (direction, protocol, port
# range, destination); the description is a mutable annotation, not part of that key — which is why
# UpdateSecurityGroupRuleDescriptionsEgress exists as a separate API. Listing the same tuple once
# per business reason does NOT produce several rules: AWS accepts the first and rejects the rest
# with InvalidPermission.Duplicate, and which one wins is decided nondeterministically by
# Terraform's parallelism, so the apply never converges. The per-destination story lives in the
# README table instead, and `egress_rules` carries a validation that fails the plan if two rules
# ever resolve to the same tuple again.
#
# SG RULES ARE A UNION, so the narrowly scoped rules below (the S3 prefix list, the ssm/logs peer
# SG) are shadowed by the 0.0.0.0/0:443 floor and change effective permissions by exactly nothing.
# They are kept as documentation of intent — don't delete them as dead weight, and don't read them
# as enforcement. They become real controls only if the 443 floor is ever narrowed.
#
# See deploy/providers/AWS/README.md for the full rationale per destination.
locals {
  # Every entry carries the full attribute set (unused selectors explicitly null) so Terraform's
  # object-type unification across this heterogeneous list doesn't choke on "inconsistent
  # conditional result types".
  trust_egress_rule_defaults = {
    protocol                 = "tcp"
    cidr_blocks              = null
    source_security_group_id = null
    prefix_list_ids          = null
  }

  trust_egress_rules = [
    for r in [
      # The 443 floor. One rule, because every destination behind it is the same AWS tuple:
      # Central Hub API (CloudFront), GHCR, Docker Hub, Hugging Face seeding, download.docker.com,
      # awscli.amazonaws.com, apt/esm over HTTPS, PyPI (seed_trust.sh's `uv run`), the
      # us-east-1 CloudWatch-agent .deb, and the AWS APIs with no VPC endpoint
      # (ssmmessages, ec2messages, monitoring).
      {
        port        = 443
        cidr_blocks = ["0.0.0.0/0"]
        description = "HTTPS to any destination: hub API (CloudFront), GHCR, Docker Hub, HF seeding, apt/esm, PyPI + download.pytorch.org, install mirrors, and AWS APIs with no VPC endpoint. See the README egress table."
      },
      {
        port        = 80
        cidr_blocks = ["0.0.0.0/0"]
        description = "HTTP to any destination: Ubuntu apt mirrors - package install/upgrade"
      },
      # Shadowed by the 443 floor above (SG rules are a union) - documentation of intent, not
      # enforcement. Kept so the intended scope survives if that floor is ever narrowed.
      {
        port            = 443
        prefix_list_ids = [data.aws_ec2_managed_prefix_list.s3.id]
        description     = "S3 (AI Centre FL kit sync) - AWS-managed prefix list, not a raw CIDR"
      },
      # NOT shadowed by anything — this is the one destination the 443 floor does not cover, so
      # it has to be 0.0.0.0/0 rather than the NLB's peer SG. The FL server NLB is
      # internet-facing (public subnets, no `internal = true`), so its DNS resolves to PUBLIC IPs
      # even from inside the VPC, and the trust EC2 (private subnet, no public IP) reaches them
      # out through the NAT gateway — which is exactly what the NLB's own ingress rule
      # allowlisting nat_public_ips[0]/32 attests. A peer-SG destination matches only the private
      # IPs of ENIs carrying that SG, so it would match none of that traffic and silently drop
      # every FL connection while image pulls, hub polling and SSM all stayed healthy. Narrow
      # this only once the NLB itself becomes `internal` (see the TODO on that module), or by
      # giving it static per-AZ EIPs via subnet_mapping and allowlisting those /32s.
      {
        port        = var.FL_SERVER_PORT
        cidr_blocks = ["0.0.0.0/0"]
        description = "FL server NLB - FL training traffic (internet-facing NLB, reached via NAT)"
      },
      # Inert in both directions: AWS documents that security groups cannot filter traffic to the
      # Route 53 Resolver (the VPC+2 address / AmazonProvidedDNS), so these two neither permit
      # nor restrict name resolution. Kept as documentation of intent. The control that actually
      # satisfies #876's DNS criterion is the ABSENCE of a 0.0.0.0/0:53 rule, which does block
      # third-party resolvers such as 8.8.8.8 — so don't debug a DNS fault against these rules,
      # and don't "fix" one by widening them.
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
  ]
}

module "trust_security_group" {
  source      = "./modules/secgroup"
  name        = "trust-security-group"
  vpc_id      = local.vpc_id
  description = "Security group for FLIP Trust EC2 instance (no inbound - access via SSM Session Manager and SSM port forwarding)"

  ingress_rules      = []
  block_all_outbound = true
  egress_rules       = local.trust_egress_rules
  tags               = { FlipSG = "true" }
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

# IAM Role for the Trust EC2.
#
# The Trust host runs trust-api, imaging-api, data-access-api, fl-client, XNAT,
# Orthanc and the OMOP DB — none of those services use boto3. The only AWS
# call from the Trust host is `aws s3 sync` against the AI Centre bucket
# during Ansible provisioning to fetch the FL participant kit (see the
# FLARE/Flower kit-download tasks in deploy/providers/AWS/site.yml).
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
#      (trust/deploy/ansible/onprem.yml) already works this way:
#      the operator stages the kit on their workstation via
#      `make add-local-trust` and rsyncs it onto the host. Applying the same
#      pattern to AWS-hosted trusts would remove S3 entirely from the Trust
#      role's blast radius.
module "trust_ec2_role" {
  source      = "terraform-aws-modules/iam/aws//modules/iam-assumable-role"
  version     = "~> 5.0"
  role_name   = "trust-ec2-role"
  create_role = "true"
  # FLIP#962: the CI apply role may only create a boundary-carrying role.
  role_permissions_boundary_arn = local.iam_permissions_boundary_arn
  trusted_role_services         = ["ec2.amazonaws.com"]
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
        # Scoped to the prefixes this host actually reads, mirroring S3ListAicentreBucketForKit
        # in iam_ecs.tf. Bucket-wide list/read was largely inert before the kms:Decrypt grant
        # below (nothing encrypted could be read at all); granting decrypt completes the
        # capability, so the scope has to be narrowed in the same change. The bucket also holds
        # every net's kits, the fl-server's own kit, the FLARE provisioning CA registry and
        # the licensed OMOP vocabulary — and this is the host that runs researcher-submitted
        # FL code, so it is the role that least deserves bucket-wide read.
        #
        # The narrowing deliberately stops at the kit-type prefix, i.e. within these trees any
        # trust host can still read any trust's kit. Slot-level (Trust_<n>) scoping is not
        # expressible here: one shared trust-ec2-role serves every trust host and the slot is
        # an Ansible-runtime value IAM never sees. Date-level scoping via var.flare_kit_date /
        # var.flower_kit_date was considered and deferred: only one of the two is non-empty
        # per backend (naive interpolation yields a dead "//*" ARN for the other, so it needs
        # conditional resource lists), and it couples `terraform apply` to the Ansible
        # -e fl_kit_date value — re-staging kits at a new date against stale IAM would
        # hard-fail the exact task this change fixes. Net effect is still a narrowing: the
        # pre-#1009 statement granted GetObject on the entire bucket. The residual is tracked
        # in FLIP#1164, which carries this reasoning and the per-trust-role route that makes
        # slot scoping expressible — this comment is not the only record of the deferral.
        #
        # Deliberately no s3:GetBucketLocation: a GetBucketLocation request carries no
        # s3:prefix context key, so under this condition the grant could never authorize
        # anything, and splitting it out unconditioned would widen the role for a call
        # nothing on this host makes — the region is supplied by the instance's own
        # configuration. Kit and vocab staging were verified on the stag EC2 trust with
        # exactly the actions below.
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.aicentre_bucket.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = [
              "fl-flare-participant-kits/*", "fl-flare-participant-kits",
              "fl-flower-participant-kits/*", "fl-flower-participant-kits",
              "vocab/*", "vocab",
            ]
          }
        }
      },
      {
        # The three prefixes above: the two participant-kit trees (site.yml kit staging) and
        # the licensed OMOP core-vocabulary bundle (site.yml, "download core vocabulary bundle
        # from S3 (instance role)").
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          "${aws_s3_bucket.aicentre_bucket.arn}/fl-flare-participant-kits/*",
          "${aws_s3_bucket.aicentre_bucket.arn}/fl-flower-participant-kits/*",
          "${aws_s3_bucket.aicentre_bucket.arn}/vocab/*",
        ]
      },
      {
        # The bucket is SSE-KMS with the app CMK (services.tf, key defined in kms.tf),
        # which declares no key policy and so relies on the AWS default that delegates
        # authorization to IAM. s3:GetObject alone is therefore not enough: without this
        # statement every read of an encrypted object fails AccessDenied on kms:Decrypt.
        # That matters more than it looks, because the kit-staging task in site.yml wipes
        # its destination before fetching — a failed read leaves the host with no kit
        # rather than the previous one. Default bucket encryption applies only to objects
        # written after it was enabled, which is why kits predating the CMK still download
        # with s3:GetObject alone and this gap went unnoticed. Decrypt is the whole read
        # path: kms:DescribeKey returns key metadata S3 never asks this role for, so it is
        # deliberately absent. The symmetry with the ECS task roles (iam_ecs.tf) that would
        # otherwise argue for carrying it does not hold — those roles also hold
        # kms:GenerateDataKey/GenerateDataKeyWithoutPlaintext because they write objects,
        # which this role deliberately cannot. This is the host that runs
        # researcher-submitted training code, so it holds the least of the three.
        # See FLIP#965.
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [aws_kms_key.flip_app_key.arn]
        # The CMK also encrypts Secrets Manager (kms.tf), so constrain the grant to
        # decrypts S3 performs on this host's behalf. Reading a secret would still need
        # secretsmanager:GetSecretValue, which this role does not have — this keeps that
        # true if the role ever gains it.
        Condition = {
          StringEquals = {
            "kms:ViaService" = "s3.${var.AWS_REGION}.amazonaws.com"
          }
        }
      },
    ]
  })
}

# The Trust EC2 still runs application containers and ships system + Docker
# logs through its CloudWatch agent. The Central Hub bastion has no app logs.
resource "aws_cloudwatch_log_group" "flip_trust_log_group" {
  name              = "/aws/ec2/flip-trust"
  retention_in_days = local.log_retention_days
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
  # Container port per backend (FLARE: FL_SERVER_PORT; Flower: SuperLink
  # Fleet 9092 — see local.fl_server_container_port in ecs_tasks.tf). The
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


###################
# Trust
###################
module "trust_ec2" {
  # Optional: a hub-only deployment (var.deploy_trust_ec2=false, `make
  # full-deploy-hub-only`) provisions no cloud trust host — every trust then
  # runs on-prem and joins via register-trusts.
  count  = var.deploy_trust_ec2 ? 1 : 0
  source = "./modules/trust_ec2"

  name_prefix   = "trust"
  instance_type = "t3.xlarge"
  key_name      = aws_key_pair.host_key.key_name
  subnet_id     = element(local.app_subnet_ids, 0)

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
