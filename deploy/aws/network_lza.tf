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
# On an LZA-governed workload account the VPC layer is provisioned by the AWS
# Accelerator pipeline (londonaicentre/lza) and the GRNETSEC2 SCP denies
# CreateVpc / CreateSubnet / CreateInternetGateway / AllocateAddress /
# CreateVpcEndpoint to everything but that pipeline (GRTGWVPN makes even
# route-table edits pipeline-only), so this stack cannot create its own network:
# the platform VPC + subnets are discovered by Name tag below.
#
# Subnet semantics on the LZA VPC (WP0 probe findings on FLIP#749):
#   - app subnets (<vpc>-app-*): default-route 0.0.0.0/0 to the Transit
#     Gateway, so they reach the central interface endpoints in the Network
#     account. Anything that talks to AWS APIs, pulls images, or terminates
#     traffic lives here: ECS tasks, the internal NLB, the RDS Proxy, EFS mount
#     targets, and the EC2 hosts.
#   - data subnets (<vpc>-data-*): local routes only — fully isolated (same-VPC
#     traffic works; no TGW, endpoint, or internet reach). Only the RDS
#     instances live here; everything that connects to them does so over
#     intra-VPC local routing.
# The lookups match ALL Name-tag hits so the multi-AZ -b subnets the platform
# team is adding appear on the next plan without a code change.

data "aws_vpc" "lza" {
  filter {
    name   = "tag:Name"
    values = [var.lza_vpc_name]
  }
}

data "aws_subnets" "lza_app" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.lza.id]
  }

  filter {
    name   = "tag:Name"
    values = ["${var.lza_vpc_name}-app-*"]
  }
}

data "aws_subnets" "lza_data" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.lza.id]
  }

  filter {
    name   = "tag:Name"
    values = ["${var.lza_vpc_name}-data-*"]
  }
}

# Network source of truth: every consumer references these locals. The subnet lists are
# sort()ed because aws_subnets returns ids in no guaranteed order and positional consumers
# (e.g. the bastion's app_subnet_ids[0]) must not be reshuffled between plans.
locals {
  vpc_id         = data.aws_vpc.lza.id
  vpc_cidr_block = data.aws_vpc.lza.cidr_block

  app_subnet_ids  = sort(data.aws_subnets.lza_app.ids)
  data_subnet_ids = sort(data.aws_subnets.lza_data.ids)
}
