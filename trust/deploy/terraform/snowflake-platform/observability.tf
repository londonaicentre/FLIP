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

# The account event table: every SPCS container's stdout, stderr and traces land here (#1584).
# The 2.x provider has no event-table resource, so the table is created through a statement pair
# whose revert drops it again (open point in README.md).
resource "snowflake_execute" "event_table" {
  execute = "CREATE EVENT TABLE IF NOT EXISTS ${local.event_table_fqn}"
  revert  = "DROP EVENT TABLE IF EXISTS ${local.event_table_fqn}"
  query   = "SHOW EVENT TABLES LIKE '${local.event_table}' IN SCHEMA ${snowflake_database.platform.name}.${local.platform_schemas.obs}"

  depends_on = [snowflake_schema.platform]
}

# Account-wide: everything logs into the platform event table. Setting an account parameter needs
# ACCOUNTADMIN, which is exactly why it lives in this tier and not in the routine apply.
resource "snowflake_account_parameter" "event_table" {
  key   = "EVENT_TABLE"
  value = local.event_table_fqn

  depends_on = [snowflake_execute.event_table]
}
