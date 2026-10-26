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

output "subnet_id" {
  description = "Subnet the trust host goes in: the site's, or the created private subnet."
  value       = local.create ? aws_subnet.private[0].id : var.existing_subnet_id
}

output "vpc_id" {
  description = "VPC of that subnet."
  value       = local.create ? aws_vpc.this[0].id : data.aws_subnet.existing[0].vpc_id
}

output "vpc_cidr" {
  description = "Primary CIDR of that VPC; its +2 address is the VPC DNS resolver."
  value       = local.create ? aws_vpc.this[0].cidr_block : data.aws_vpc.existing[0].cidr_block
}

output "nat_gateway_ids" {
  description = "The NAT gateway a created network leaves through (empty on a site subnet)."
  value       = aws_nat_gateway.this[*].id
}

output "nat_public_ip" {
  description = "The created NAT gateway's address, for reference only: the hub never checks a trust's IP (empty on a site subnet)."
  value       = try(aws_eip.nat[0].public_ip, "")
}
