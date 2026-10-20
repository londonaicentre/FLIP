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
# Observability is the account event table plus views over it (#1584); no Loki, Grafana or Alloy
# container runs on SPCS. The provider has no event-table resource in 2.x, so the table is created
# through a statement pair whose revert drops it again (open point in README.md).
resource "snowflake_execute" "event_table" {
  execute = "CREATE EVENT TABLE IF NOT EXISTS ${snowflake_database.trust.name}.${local.schemas.obs}.${local.event_table}"
  revert  = "DROP EVENT TABLE IF EXISTS ${snowflake_database.trust.name}.${local.schemas.obs}.${local.event_table}"
  query   = "SHOW EVENT TABLES LIKE '${local.event_table}' IN SCHEMA ${snowflake_database.trust.name}.${local.schemas.obs}"

  depends_on = [snowflake_schema.trust]
}

# Account-wide: everything logs into this trust's event table. One trust per account is the tested
# shape; sharing an account means the last apply wins, which the README records.
resource "snowflake_account_parameter" "event_table" {
  key   = "EVENT_TABLE"
  value = "${snowflake_database.trust.name}.${local.schemas.obs}.${local.event_table}"

  depends_on = [snowflake_execute.event_table]
}
