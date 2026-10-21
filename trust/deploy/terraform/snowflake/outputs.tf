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

# Everything #1582's Makefile and specs need, by name. No credential, no account identifier,
# nothing that is a secret. The role, user and integration names are derived by convention from
# trust_code, exactly as the platform root derives them: the two roots share no state.
output "database_name" {
  description = "The trust's database."
  value       = snowflake_database.trust.name
}

output "schema_names" {
  description = "Schema names by role (app, omop, stages, obs)."
  value       = { for key, schema in snowflake_schema.trust : key => schema.name }
}

output "image_repository_name" {
  description = "Fully qualified image repository; `make mirror-images` resolves its URL with SHOW IMAGE REPOSITORIES."
  value       = snowflake_image_repository.images.fully_qualified_name
}

output "image_repository_url" {
  description = "Registry URL to push mirrored images to, from SHOW IMAGE REPOSITORIES."
  value       = try(snowflake_image_repository.images.show_output[0].repository_url, "")
}

output "compute_pool_names" {
  description = "Compute pools by purpose. The GPU pool is absent unless enable_gpu_pool is set."
  value = merge(
    {
      service = local.pool
      job     = local.job_pool
    },
    var.enable_gpu_pool ? { gpu = local.gpu_pool } : {}
  )
}

output "stage_names" {
  description = "Fully qualified stage names by stage, for the spec templates' volume mounts."
  value       = { for name, stage in snowflake_stage_internal.trust : name => stage.fully_qualified_name }
}

output "warehouse_name" {
  description = "The trust warehouse."
  value       = snowflake_warehouse.trust.name
}

output "role_names" {
  description = "The five FLIP roles by purpose, derived from trust_code. Created by the platform root, not here."
  value       = local.roles
}

output "deploy_user_name" {
  description = "The deploy SERVICE user this root is applied as. Created by the platform root."
  value       = local.deploy_user
}

output "external_access_integration_name" {
  description = "The hub egress integration created by the platform root, or an empty string when it is off."
  value       = var.enable_hub_egress ? local.hub_eai : ""
}

output "fl_net_image_prefixes" {
  description = "Per-net prefixes on the IMAGES stage, one per FL network."
  value       = [for n in local.fl_nets : "net-${n}/"]
}

# Read by the node Makefile in #1582 to decide whether it creates the services itself (the `snow`
# CLI fallback) or expects this root to own them. False until #1582 lands.
output "manage_services" {
  description = "Whether this root owns the SPCS services. Placeholder for #1582; false today."
  value       = var.manage_services
}
