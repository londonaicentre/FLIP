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

# Names only, never a secret. The CI users and roles go in the sf-* GitHub environment.

output "external_access_integration_name" {
  description = "The hub egress integration, or an empty string when enable_hub_egress is false."
  value       = var.enable_hub_egress ? local.hub_eai : ""
}

output "resource_monitor_name" {
  description = "The resource monitor the workload root attaches to its warehouse."
  value       = snowflake_resource_monitor.trust.name
}

output "warehouse_name" {
  description = "The trust warehouse, with the resource monitor attached."
  value       = snowflake_warehouse.trust.name
}

output "ci_role_name" {
  description = "The role CI applies the platform root as."
  value       = snowflake_account_role.platform_ci.name
}

output "ci_read_role_name" {
  description = "The read-only role CI plans the platform root as."
  value       = snowflake_account_role.platform_ci_read.name
}

output "ci_users" {
  description = "The CI service users (GitHub OIDC only) and the subject each one trusts."
  value       = { for k, u in local.ci_users : k => { name = u.name, role = u.role, subject = u.subject } }
}
