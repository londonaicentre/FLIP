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

locals {
  create = var.existing_subnet_id == ""
}

data "aws_availability_zones" "available" {
  count = local.create && var.availability_zone == "" ? 1 : 0
  state = "available"
}

# On a site subnet the module reads the VPC only, for the DNS resolver address the host's
# security group allows.
data "aws_subnet" "existing" {
  count = local.create ? 0 : 1
  id    = var.existing_subnet_id
}

data "aws_vpc" "existing" {
  count = local.create ? 0 : 1
  id    = data.aws_subnet.existing[0].vpc_id
}

resource "aws_vpc" "this" {
  # checkov:skip=CKV2_AWS_11:A created network holds one trust host for a standalone or test install; flow logs are the site's choice on its own VPC
  count                = local.create ? 1 : 0
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.name}-vpc" }
}

# Strips the default security group's rules, so nothing that lands in it by default is reachable.
resource "aws_default_security_group" "this" {
  count  = local.create ? 1 : 0
  vpc_id = aws_vpc.this[0].id
  tags   = { Name = "${var.name}-default-closed" }
}

locals {
  az = var.availability_zone != "" ? var.availability_zone : try(data.aws_availability_zones.available[0].names[0], "")
}

resource "aws_subnet" "public" {
  count                   = local.create ? 1 : 0
  vpc_id                  = aws_vpc.this[0].id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = local.az
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.name}-nat" }
}

resource "aws_subnet" "private" {
  count                   = local.create ? 1 : 0
  vpc_id                  = aws_vpc.this[0].id
  cidr_block              = var.private_subnet_cidr
  availability_zone       = local.az
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.name}-node" }
}

resource "aws_internet_gateway" "this" {
  count  = local.create ? 1 : 0
  vpc_id = aws_vpc.this[0].id
  tags   = { Name = "${var.name}-igw" }
}

# Outbound for a network this module creates: a NAT gateway whose Elastic IP is created and
# destroyed with the stack. Nothing keeps an address across rebuilds, because the hub admits a
# trust by its credentials, never by source IP. On a site subnet the site's own egress applies.
resource "aws_eip" "nat" {
  count  = local.create ? 1 : 0
  domain = "vpc"
  tags   = { Name = "${var.name}-nat" }
}

resource "aws_nat_gateway" "this" {
  count         = local.create ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = "${var.name}-nat" }
  depends_on    = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  count  = local.create ? 1 : 0
  vpc_id = aws_vpc.this[0].id
  tags   = { Name = "${var.name}-public" }

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this[0].id
  }
}

resource "aws_route_table_association" "public" {
  count          = local.create ? 1 : 0
  subnet_id      = aws_subnet.public[0].id
  route_table_id = aws_route_table.public[0].id
}

resource "aws_route_table" "private" {
  count  = local.create ? 1 : 0
  vpc_id = aws_vpc.this[0].id
  tags   = { Name = "${var.name}-private" }

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[0].id
  }
}

resource "aws_route_table_association" "private" {
  count          = local.create ? 1 : 0
  subnet_id      = aws_subnet.private[0].id
  route_table_id = aws_route_table.private[0].id
}
