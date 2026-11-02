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

# The account's security objects (password and session policies, the network rule) live in their
# own database, owned by the bootstrap tier. They used to sit in FLIP_PLATFORM.POLICIES; that
# database now belongs to FLIP_PLATFORM_CI, and a role that owns the schema a policy lives in can
# replace the policy, so the policies had to leave it.
resource "snowflake_database" "admin" {
  name    = local.admin_database
  comment = "FLIP platform bootstrap (epic #1589): account security objects. Break glass only."

  drop_public_schema_on_creation = true
}

resource "snowflake_schema" "admin_policies" {
  database = snowflake_database.admin.name
  name     = "POLICIES"
  comment  = "FLIP platform bootstrap: account password, session and network policies."

  with_managed_access = true
}
