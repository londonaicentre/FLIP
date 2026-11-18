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
# Every internal stage is server-side encrypted. SNOWFLAKE_SSE rather than SNOWFLAKE_FULL because
# SPCS stage volumes cannot mount a client-side encrypted stage.
resource "snowflake_stage_internal" "trust" {
  for_each = toset(local.stage_names)

  database = snowflake_database.trust.name
  schema   = snowflake_schema.trust["stages"].name
  name     = each.value
  comment  = "FLIP ${local.code}: ${each.value} stage (SSE)."

  encryption {
    snowflake_sse {}
  }

  directory {
    enable = "true"
  }
}
