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
  description = "The node subnet: the one created here, or existing_subnet_id passed through."
  value       = local.create ? azurerm_subnet.this[0].id : var.existing_subnet_id
}

output "nat_public_ip_address" {
  description = "The NAT gateway's outbound address on a created network; empty on a site subnet, whose egress the site owns."
  value       = local.create ? azurerm_public_ip.nat[0].ip_address : ""
}

output "nat_gateway_ids" {
  description = "The NAT gateway on a created network (one id), none on a site subnet."
  value       = azurerm_nat_gateway.this[*].id
}
