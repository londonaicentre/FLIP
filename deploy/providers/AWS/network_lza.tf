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

############################
# Platform-managed network (LZA) — FLIP#749
############################
#
# On the LZA FLIPProduction account (893493035022) the VPC layer is provisioned
# by the AWS Accelerator pipeline (londonaicentre/lza) and the GRNETSEC2 SCP
# denies CreateVpc / CreateSubnet / CreateInternetGateway / AllocateAddress /
# CreateVpcEndpoint to everything but that pipeline (GRTGWVPN makes even
# route-table edits pipeline-only), so this stack cannot create its own network
# there. When var.lza_managed_network is set the VPC module is skipped
# (create_vpc = false in main.tf) and the platform VPC + subnets are discovered
# by Name tag below.
#
# Subnet semantics on the LZA VPC (WP0 probe findings on FLIP#749):
#   - app subnets (<vpc>-app-*): default-route 0.0.0.0/0 to the Transit
#     Gateway, so they reach the central interface endpoints in the Network
#     account. Anything that talks to AWS APIs, pulls images, or terminates
#     traffic lives here: ECS tasks, the internal ALB, the RDS Proxy, EFS mount
#     targets, and the EC2 hosts.
#   - data subnets (<vpc>-data-*): local routes only — fully isolated (same-VPC
#     traffic works; no TGW, endpoint, or internet reach). Only the RDS
#     instances live here; everything that connects to them does so over
#     intra-VPC local routing.
# The lookups match ALL Name-tag hits so the multi-AZ -b subnets the platform
# team is adding appear on the next plan without a code change.

data "aws_vpc" "lza" {
  count = var.lza_managed_network ? 1 : 0

  filter {
    name   = "tag:Name"
    values = [var.lza_vpc_name]
  }
}

data "aws_subnets" "lza_app" {
  count = var.lza_managed_network ? 1 : 0

  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.lza[0].id]
  }

  filter {
    name   = "tag:Name"
    values = ["${var.lza_vpc_name}-app-*"]
  }
}

data "aws_subnets" "lza_data" {
  count = var.lza_managed_network ? 1 : 0

  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.lza[0].id]
  }

  filter {
    name   = "tag:Name"
    values = ["${var.lza_vpc_name}-data-*"]
  }
}

# Network source of truth for BOTH paths — every consumer references these
# locals; only this file and main.tf's module "flip_vpc" know which path is
# active. The data-lookup branches are sort()ed because aws_subnets returns ids
# in no guaranteed order and positional consumers (e.g. the bastion's
# app_subnet_ids[0]) must not be reshuffled between plans; the legacy branches
# keep the module outputs untouched so existing state never sees an order
# change.
locals {
  vpc_id         = var.lza_managed_network ? data.aws_vpc.lza[0].id : module.flip_vpc.vpc_id
  vpc_cidr_block = var.lza_managed_network ? data.aws_vpc.lza[0].cidr_block : var.vpc_cidr

  # Legacy envs have no app/data split — both roles map onto the private subnets.
  app_subnet_ids  = var.lza_managed_network ? sort(data.aws_subnets.lza_app[0].ids) : module.flip_vpc.private_subnets
  data_subnet_ids = var.lza_managed_network ? sort(data.aws_subnets.lza_data[0].ids) : module.flip_vpc.private_subnets
}
