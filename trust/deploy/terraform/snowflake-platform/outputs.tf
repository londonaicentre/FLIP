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

# Names only: no credential, no account identifier, nothing that is a secret. The workload root
# does NOT read these (there is no remote-state coupling between the roots); it derives the same
# names from the same trust_code. They are printed so an operator can confirm the two agree.
output "role_names" {
  description = "The five FLIP roles, by purpose. FLIP_<CODE>_ADMIN is retired."
  value       = local.roles
}

output "deploy_user_name" {
  description = "The deploy SERVICE user. Its private key lives outside Terraform."
  value       = snowflake_service_user.deploy.name
}

output "deploy_account_privileges" {
  description = "The account privileges the workload role holds, and the complete list of them."
  value       = local.deploy_account_privileges
}

output "external_access_integration_name" {
  description = "The hub egress integration, or an empty string when enable_hub_egress is false."
  value       = var.enable_hub_egress ? local.hub_eai : ""
}

output "event_table_name" {
  description = "Fully qualified event table the account logs to (#1584)."
  value       = local.event_table_fqn
}

output "platform_database_name" {
  description = "The small platform-owned database holding the event table and the account policies."
  value       = snowflake_database.platform.name
}

output "resource_monitor_name" {
  description = "The resource monitor the workload root attaches to its warehouse."
  value       = snowflake_resource_monitor.trust.name
}
