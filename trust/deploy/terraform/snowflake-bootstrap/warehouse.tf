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

# The trust warehouse, with the resource monitor attached. Only ACCOUNTADMIN may create or attach a
# resource monitor, so the warehouse lives in the bootstrap tier. The platform root (applied by CI as
# FLIP_PLATFORM_CI) grants its use to the trust roles, by name, under the grant option below.
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
# One XSMALL warehouse. It serves the OMOP reads and the nightly task; SPCS compute is billed by
# the pools, not here.
# The trust warehouse lives in the PLATFORM tier (decision 2026-10-22, epic #1589) because its
# cost guard does: assigning a resource monitor to a warehouse needs ACCOUNTADMIN, so a warehouse the
# deploy role owned could not carry one, and one it could recreate could shed it. The platform owns
# the warehouse with FLIP_<CODE>_RM attached; the deploy role gets USAGE, OPERATE and MONITOR only
# (no MODIFY, no OWNERSHIP), so the deploy key can neither detach the monitor nor rebuild around it.
resource "snowflake_warehouse" "trust" {
  name           = local.warehouse
  comment        = "FLIP ${local.code}: trust warehouse (platform-owned, resource monitor attached)."
  warehouse_size = "XSMALL"
  warehouse_type = "STANDARD"

  auto_suspend        = var.warehouse_auto_suspend_secs
  auto_resume         = "true"
  initially_suspended = true

  min_cluster_count = 1
  max_cluster_count = 1

  statement_timeout_in_seconds        = var.warehouse_statement_timeout_secs
  statement_queued_timeout_in_seconds = var.warehouse_statement_timeout_secs

  resource_monitor = snowflake_resource_monitor.trust.name
}

# What lets the CI role pass the warehouse on: exactly the privileges the platform root grants the
# trust roles, WITH GRANT OPTION, and never OWNERSHIP or MODIFY.
resource "snowflake_grant_privileges_to_account_role" "ci_warehouse" {
  account_role_name = snowflake_account_role.platform_ci.name
  privileges        = local.ci_warehouse_privileges
  with_grant_option = true

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.trust.name
  }
}
