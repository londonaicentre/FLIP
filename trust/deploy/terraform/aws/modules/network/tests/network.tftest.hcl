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

mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = { names = ["eu-west-2a", "eu-west-2b"] }
  }
  mock_data "aws_subnet" {
    defaults = { vpc_id = "vpc-0site" }
  }
  mock_data "aws_vpc" {
    defaults = { cidr_block = "10.20.0.0/16" }
  }
}

variables {
  name = "flip-trust-test"
}

# apply, not plan: with the mock provider nothing is created, and the route's NAT gateway id is known.
run "default_creates_a_network_that_leaves_through_its_own_nat_gateway" {
  command = apply

  assert {
    condition     = length(aws_vpc.this) == 1 && length(aws_subnet.private) == 1 && length(aws_subnet.public) == 1
    error_message = "Without existing_subnet_id the module creates a VPC with a private and a NAT subnet."
  }
  assert {
    condition     = length(aws_nat_gateway.this) == 1 && length(aws_eip.nat) == 1 && length(aws_internet_gateway.this) == 1
    error_message = "A created network gets one NAT gateway, its Elastic IP and an internet gateway, created with the stack."
  }
  assert {
    condition     = one([for r in aws_route_table.private[0].route : r.nat_gateway_id != null && r.cidr_block == "0.0.0.0/0"])
    error_message = "The node subnet's default route is the NAT gateway."
  }
  assert {
    condition     = !aws_subnet.private[0].map_public_ip_on_launch && !aws_subnet.public[0].map_public_ip_on_launch
    error_message = "Neither subnet hands out public IPs."
  }
  assert {
    condition     = aws_subnet.private[0].availability_zone == "eu-west-2a"
    error_message = "With no availability_zone the subnets go in the region's first."
  }
  assert {
    condition     = length(aws_default_security_group.this) == 1
    error_message = "The created VPC's default security group is stripped of its rules."
  }
}

run "existing_subnet_creates_nothing" {
  command = plan

  variables {
    existing_subnet_id = "subnet-0site"
  }

  assert {
    condition     = length(aws_vpc.this) + length(aws_subnet.private) + length(aws_subnet.public) + length(aws_internet_gateway.this) == 0
    error_message = "With existing_subnet_id the module creates no VPC, subnet or internet gateway."
  }
  assert {
    condition     = length(aws_nat_gateway.this) == 0 && length(aws_eip.nat) == 0 && output.nat_public_ip == ""
    error_message = "On a site subnet the module creates no NAT gateway and no Elastic IP: the site's egress applies."
  }
  assert {
    condition     = output.subnet_id == "subnet-0site" && output.vpc_id == "vpc-0site" && output.vpc_cidr == "10.20.0.0/16"
    error_message = "The site's subnet, its VPC and the VPC's CIDR are passed through."
  }
}
