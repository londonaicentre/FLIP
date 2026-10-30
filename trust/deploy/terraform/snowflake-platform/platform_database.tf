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

# One small database owned by the platform tier. It exists because an event table needs a database
# and the account EVENT_TABLE parameter must point somewhere that outlives any one trust: putting
# it in FLIP_TRUST_<CODE> would mean a workload `make destroy` silently turned account logging off.
resource "snowflake_database" "platform" {
  name    = local.platform_database
  comment = "FLIP platform tier (#1589): account-wide observability and policy objects."

  # PUBLIC is dropped at creation: no schema in this tree is world-usable.
  drop_public_schema_on_creation = true

  data_retention_time_in_days = var.platform_database_retention_days
}

resource "snowflake_schema" "platform" {
  for_each = local.platform_schemas

  database = snowflake_database.platform.name
  name     = each.value
  comment  = "FLIP platform: ${each.key} schema."

  # Managed access: only the schema owner grants on its objects.
  with_managed_access = true
}
