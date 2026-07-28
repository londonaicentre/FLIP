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

# e2e-lza Terraform root: LZA ingress end-to-end test stack.
#
# Proves the WORKLOAD-ACCOUNT half of the LZA two-tier ingress design with
# dummy ECS services, inside an LZA-provisioned sealed workload VPC (no IGW,
# no NAT, no public subnets):
#
#   Web: user → CloudFront + WAF (networking acct) → internal NLB (networking
#        acct, TCP:443 passthrough) → firewall → TGW → THIS internal ALB
#        (TLS terminates here in the target design; plain HTTP for the test)
#        → ECS e2e-web
#   FL:  trust FL client (internet, mTLS gRPC) → internet-facing NLB
#        (networking acct, TCP:8002 passthrough) → firewall → TGW → THIS
#        internal NLB → ECS e2e-fl
#
# LZA-estate reality (differs from a self-managed account — see README):
#   - The VPC, subnets, TGW attachment, S3/DynamoDB gateway endpoints and
#     central interface endpoints are ALL provisioned by the LZA pipeline
#     (londonaicentre/lza network-config.yaml "prod" VPC template, applied to
#     every account in the Workloads/Prod OU). The GRNETSEC2 SCP denies
#     VPC/subnet/NAT/IGW/EIP/endpoint creation to this stack's role, so this
#     root DATA-SOURCES the network layer and creates only app-layer
#     resources: security groups, load balancers, ECS, ECR, the probe, and
#     the SSM handoff parameters.
#   - The web leg's internal ALB needs subnets in ≥ 2 AZs; the LZA prod VPC
#     template is single-AZ today, so the web leg is gated behind
#     var.enable_web_leg (default false) until the multi-AZ template change
#     lands. The FL leg (NLB) is single-AZ-capable and applies now.
#   - CloudFront VPC origins are SCP-denied for workload roles (lza PR #36,
#     GRCLOUDFRONTVPCORIGIN) — the two-tier chain tested here is the only
#     sanctioned web ingress path.
#
# The networking-account half (CloudFront, edge NLBs, TGW, firewall) lives in
# the private londonaicentre/lza + aicentre-lza-iac repos; the contract with
# it is the SSM parameters in parameter_store.tf. This stack must never be
# folded into the prod/stag root (../) — it targets the LZA workload account
# and has its own state key (backend.tf). See README.md for the verification
# runbook and the open questions that need the networking-account owner's
# sign-off.

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
# Naming + ports
############################

locals {
  name_prefix = "e2e-lza"

  # Ports exposed to the networking account (published via SSM in
  # parameter_store.tf). web_port becomes 443 once the TLS follow-up in
  # README.md lands; fl_port mirrors the real FL server's gRPC port.
  web_port = 80
  fl_port  = 8002

  # http-echo's listen port for the web service — the ALB translates
  # web_port → this, mirroring how the prod ALB fronts flip-api. The FL
  # service listens on fl_port directly so the TCP leg is port-faithful
  # end to end.
  web_container_port = 5678
}

############################
# LZA-provisioned network layer (data-sourced, never created here)
############################
# The sealed VPC comes from the LZA "prod" VPC template: IPAM-allocated
# CIDR, internetGateway=false, TGW attachment to the Network account,
# S3+DynamoDB gateway endpoints, useCentralEndpoints=true (interface
# endpoints for ec2/ecr/kms/logs/monitoring/ssm* + more live centrally in
# the Network account's endpoints VPC and resolve here via shared private
# hosted zones). The acceptance criterion in README.md is that the account
# STAYS sealed — this stack must never grow a NAT/IGW workaround.

data "aws_vpc" "lza_prod" {
  filter {
    name   = "tag:Name"
    values = [var.lza_vpc_name]
  }
}

# App-tier subnets ("<prefix>-prod-app-<az>"). Today this matches a single
# eu-west-2a subnet; when the multi-AZ template change lands, the new -b
# subnet is picked up automatically on the next plan (which is what unblocks
# var.enable_web_leg).
data "aws_subnets" "app" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.lza_prod.id]
  }
  filter {
    name   = "tag:Name"
    values = ["*-prod-app-*"]
  }
}

locals {
  # Sorted for stable ordering across plans (subnet discovery order is not
  # guaranteed; an unsorted list would replan the probe's subnet).
  app_subnet_ids = sort(data.aws_subnets.app.ids)
}
