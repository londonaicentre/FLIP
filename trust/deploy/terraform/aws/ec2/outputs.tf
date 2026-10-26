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

output "instance_id" {
  description = "The trust host. The Makefile's ssh-config and the aws-ec2.yml inventory read it."
  value       = aws_instance.node.id
}

output "ssm_session_command" {
  description = "Open a shell on the host."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.node.id}"
}

output "private_ip" {
  description = "The host's private address. It has no public one."
  value       = aws_instance.node.private_ip
}

output "subnet_id" {
  description = "Subnet the host runs in."
  value       = module.network.subnet_id
}

output "nat_public_ip" {
  description = "Address a created network leaves from (empty on a site subnet). The hub never checks it."
  value       = module.network.nat_public_ip
}
