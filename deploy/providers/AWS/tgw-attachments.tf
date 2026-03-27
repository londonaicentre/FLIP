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
# Cross-Account TGW Attachment
############################

# Attach Prod/Staging VPC to centralized Transit Gateway owned by Networking Account
# This allows Prod/Staging VPC to route through the Networking Account's TGW and reach on-prem via VPN
#
# Architecture:
# Prod/Staging VPC <-> TGW Attachment <-> Networking Account TGW <-> VPN Connection <-> On-Premises
resource "aws_ec2_transit_gateway_vpc_attachment" "flip_vpc_attachment" {
  transit_gateway_id = var.networking_account_tgw_id # Reference external TGW from Networking Account
  vpc_id             = module.flip_vpc.vpc_id
  subnet_ids         = module.flip_vpc.private_subnets

  tags = {
    Name = "flip-vpc-tgw-attachment"
  }

  lifecycle {
    prevent_destroy = true # Persistent network backbone connection to Networking Account TGW
  }
}

# Enable VPC CIDR route propagation to Networking Account TGW route table
# This automatically advertises the VPC prefix to the TGW routing table,
# allowing on-prem traffic to discover the path to this VPC
resource "aws_ec2_transit_gateway_route_table_propagation" "flip_vpc_propagation" {
  count                          = var.enable_tgw_route_propagation ? 1 : 0
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.flip_vpc_attachment.id
  transit_gateway_route_table_id = var.networking_account_tgw_route_table_id

  lifecycle {
    prevent_destroy = true # Persistent routing configuration
  }
}
