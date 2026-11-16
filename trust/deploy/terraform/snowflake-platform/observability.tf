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
#
# DATA_RETENTION_TIME_IN_DAYS on an event table is Time Travel, NOT row expiry: event rows are
# never removed by Snowflake. The retention policy this issue asks for is therefore the pruning
# task below; the Time Travel window is set by the ALTER that follows, and is kept at one day so a
# pruned row is not also kept for a week in Time Travel storage.
#
# The CREATE statement is deliberately left exactly as it was: changing it would force a replace,
# and this resource's revert is DROP EVENT TABLE, so a cosmetic edit here would take the account's
# logs with it.
resource "snowflake_execute" "event_table" {
  execute = "CREATE EVENT TABLE IF NOT EXISTS ${local.event_table_fqn}"
  revert  = "DROP EVENT TABLE IF EXISTS ${local.event_table_fqn}"
  query   = "SHOW EVENT TABLES LIKE '${local.event_table}' IN SCHEMA ${snowflake_database.platform.name}.${local.platform_schemas.obs}"

  depends_on = [snowflake_schema.platform]
}

# The Time Travel window, set by its own statement rather than in the CREATE above: the CREATE is
# IF NOT EXISTS, so on an account whose event table already exists it is a no-op, and editing it
# would mean replacing the event table to change a retention setting.
resource "snowflake_execute" "event_table_time_travel" {
  execute = "ALTER TABLE ${local.event_table_fqn} SET DATA_RETENTION_TIME_IN_DAYS = 1"
  revert  = "ALTER TABLE ${local.event_table_fqn} UNSET DATA_RETENTION_TIME_IN_DAYS"
  query   = "SHOW EVENT TABLES LIKE '${local.event_table}' IN SCHEMA ${snowflake_database.platform.name}.${local.platform_schemas.obs}"

  depends_on = [snowflake_execute.event_table]
}

# Account-wide: everything logs into the platform event table. Setting an account parameter needs
# ACCOUNTADMIN, which is exactly why it lives in this tier and not in the routine apply.
#
# Note what is deliberately NOT here: no account, database or schema log-level parameter. Turning
# logging on account-wide would collect every Snowflake-internal procedure's output and bill the
# storage for it. Verbosity is set per service, in each spec's
# logExporters.eventTableConfig.logLevel, and the workload root's static guards refuse DEBUG and
# TRACE there (trust/deploy/terraform/snowflake/variables.tf, service_log_level).
resource "snowflake_account_parameter" "event_table" {
  key   = "EVENT_TABLE"
  value = local.event_table_fqn

  depends_on = [snowflake_execute.event_table]
}

# The retention policy (#1584). An event table grows forever on its own: rows are only removed by
# a DELETE. This task is that DELETE, daily, owned by the platform tier so the deploy key cannot
# lengthen the window or switch it off.
resource "snowflake_task" "prune_events" {
  database  = snowflake_database.platform.name
  schema    = local.platform_schemas.obs
  name      = local.prune_task
  warehouse = snowflake_warehouse.trust.name
  started   = true
  comment   = "FLIP platform: event-table retention (#1584). Snowflake never expires event rows by itself."

  schedule {
    using_cron = "${var.event_table_prune_cron} ${var.event_table_prune_timezone}"
  }

  sql_statement = "DELETE FROM ${local.event_table_fqn} WHERE TIMESTAMP < DATEADD('day', -${var.event_table_retention_days}, CURRENT_TIMESTAMP())"

  user_task_timeout_ms = 1800000

  depends_on = [snowflake_execute.event_table]
}
