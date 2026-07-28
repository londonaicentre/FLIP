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
# dummy ECS services, in a fresh, sealed workload account (no IGW, no NAT,
# no public subnets):
#
#   Web: user → CloudFront + WAF (networking acct) → internal NLB (networking
#        acct, TCP:443 passthrough) → firewall → TGW → THIS internal ALB
#        (TLS terminates here in the target design; plain HTTP for the test)
#        → ECS e2e-web
#   FL:  trust FL client (internet, mTLS gRPC) → internet-facing NLB
#        (networking acct, TCP:8002 passthrough) → firewall → TGW → THIS
#        internal NLB → ECS e2e-fl
#
# The networking-account half (CloudFront, edge NLBs, TGW, firewall) lives in
# the private aicentre-iac / LZA repo; the contract with it is the SSM
# parameters in parameter_store.tf. This stack must never be folded into the
# prod/stag root (../) — it targets a different account and has its own state
# key (backend.tf). See README.md for the verification runbook and the open
# questions that need the networking-account owner's sign-off.

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

  # One private subnet per AZ, carved from the IPAM-allocated CIDR.
  # newbits=4 keeps the subnets comfortably sized from any parent the IPAM
  # is likely to hand out (/16 parent → /20 subnets, /21 → /25).
  private_subnets = [for i in range(var.max_azs) : cidrsubnet(var.vpc_cidr, 4, i)]
}

############################
# Sealed VPC
############################
# Private subnets only: no IGW, no NAT, no public subnets. Every AWS API
# dependency is served by the endpoints in vpc_endpoints.tf; the only ways
# in or out are the (networking-account-owned) TGW attachment and SSM.
# The acceptance criterion in README.md is that this stays true — no
# NAT/IGW may ever appear in `terraform state list`.

data "aws_availability_zones" "available" {}

module "vpc" {
  source               = "terraform-aws-modules/vpc/aws"
  version              = "~> 6.0"
  name                 = "${local.name_prefix}-vpc"
  azs                  = slice(data.aws_availability_zones.available.names, 0, var.max_azs)
  cidr                 = var.vpc_cidr
  private_subnets      = local.private_subnets
  enable_nat_gateway   = false
  enable_dns_hostnames = true
}
