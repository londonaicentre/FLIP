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
# Transit Gateway (Networking Account - NOT CREATED HERE)
############################

# NOTE: Transit Gateway is owned and managed by the Networking Account (222257058857)
# This account (Prod/Staging) only ATTACHES its VPC to the central TGW.
# See deploy/providers/AWS/tgw-attachments.tf for cross-account attachment logic.
#
# Networking Account manages:
# - TGW creation (aws_ec2_transit_gateway)
# - TGW route table (aws_ec2_transit_gateway_route_table)
# - VPN connection to on-premises (aws_vpn_connection)
# - Route propagation and routing rules
#
# This account manages:
# - Cross-account VPC attachment (aws_ec2_transit_gateway_vpc_attachment)
