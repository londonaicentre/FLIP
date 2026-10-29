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

output "cluster_name" {
  description = "The cluster, and the kube context make credentials writes."
  value       = aws_eks_cluster.this.name
}

output "outbound_ip" {
  description = "The NAT gateway's address on a created network, for reference only: the hub never checks a trust's IP (empty on site subnets)."
  value       = try(module.network[0].nat_public_ip, "")
}

output "xnat_file_system_id" {
  description = "The EFS file system holding XNAT's archive, which the xnat-efs storage class provisions into."
  value       = aws_efs_file_system.xnat.id
}
