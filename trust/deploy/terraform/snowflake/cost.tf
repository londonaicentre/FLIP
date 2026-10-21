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

# A trust node on SPCS bills per compute-pool node while the pool runs, and resource monitors do
# NOT cover compute pools. So:
#
# 1. The platform tier's resource monitor caps warehouse credits (attached in warehouse.tf).
# 2. Compute pools suspend themselves (compute_pools.tf) and this nightly task suspends whatever
#    is still running, so a forgotten `make up` costs one evening rather than one month.
resource "snowflake_task" "suspend_pools" {
  database  = snowflake_database.trust.name
  schema    = snowflake_schema.trust["app"].name
  name      = local.suspend_task
  warehouse = snowflake_warehouse.trust.name
  started   = true
  comment   = "FLIP ${local.code}: nightly compute-pool suspend. Resource monitors do not cover SPCS."

  schedule {
    using_cron = "${var.suspend_pools_cron} ${var.suspend_pools_timezone}"
  }

  # ALTER ... SUSPEND on a pool that is already suspended is an error, so each statement is guarded
  # by IF EXISTS and the block continues past a pool that is already down.
  sql_statement = <<-SQL
    BEGIN
      ${join("\n      ", [for name in keys(local.pools) : "BEGIN ALTER COMPUTE POOL IF EXISTS ${name} SUSPEND; EXCEPTION WHEN OTHER THEN NULL; END;"])}
    END
  SQL

  user_task_timeout_ms = 300000
}
