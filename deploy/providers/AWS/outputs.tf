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
# Transit Gateway (Cross-Account Attachment)
############################

output "transit_gateway_attachment_id" {
  description = "ID of cross-account TGW attachment (VPC to Networking Account TGW)"
  value       = try(aws_ec2_transit_gateway_vpc_attachment.flip_vpc_attachment.id, null)
}


output "networking_account_tgw_id" {
  description = "Transit Gateway ID from Networking Account (centralized hub). Managed by network team."
  value       = var.networking_account_tgw_id
}

output "networking_account_id" {
  description = "AWS Account ID of the Networking Account"
  value       = var.networking_account_id
}
