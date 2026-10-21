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
# One database per trust; several trusts may share an account without sharing anything else.
resource "snowflake_database" "trust" {
  name    = local.database
  comment = "FLIP trust node ${local.code} (#1589)."

  # PUBLIC is dropped at creation: no schema in this tree is world-usable.
  drop_public_schema_on_creation = true

  data_retention_time_in_days = 1
  log_level                   = "INFO"
  trace_level                 = "OFF"
}

resource "snowflake_schema" "trust" {
  for_each = local.schemas

  database = snowflake_database.trust.name
  name     = each.value
  comment  = "FLIP ${local.code}: ${each.key} schema."

  # Managed access: only the schema owner grants on its objects, so a service that creates a table
  # cannot hand it out.
  with_managed_access = true
}
