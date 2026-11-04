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

# The only account-level grant in either root. Exactly four privileges, listed in locals.tf and
# pinned by the static guards here and in the workload root:
#
#   CREATE DATABASE, CREATE COMPUTE POOL, BIND SERVICE ENDPOINT, EXECUTE TASK
#
# Deliberately absent: CREATE ROLE, CREATE USER, CREATE INTEGRATION, CREATE NETWORK POLICY,
# MANAGE GRANTS, APPLY <policy>, MONITOR USAGE. A compromised deploy key therefore cannot mint an
# identity, open an egress path, detach an account policy or re-grant anything it does not own.
resource "snowflake_grant_privileges_to_account_role" "deploy_account" {
  account_role_name = snowflake_account_role.deploy.name
  privileges        = local.deploy_account_privileges
  on_account        = true
}

# USAGE on the egress integration, so a service spec may reference it. The integration itself is
# an account object the workload role can neither create nor alter.
resource "snowflake_grant_privileges_to_account_role" "deploy_eai" {
  count = var.enable_hub_egress ? 1 : 0

  account_role_name = snowflake_account_role.deploy.name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "INTEGRATION"
    object_name = snowflake_external_access_integration.hub[0].name
  }
}

resource "snowflake_grant_privileges_to_account_role" "svc_eai" {
  count = var.enable_hub_egress ? 1 : 0

  account_role_name = snowflake_account_role.svc.name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "INTEGRATION"
    object_name = snowflake_external_access_integration.hub[0].name
  }
}

# The observer role reads the account's logs: the platform database, its OBS schema, and the event
# table itself. No clinical data lives here.
resource "snowflake_grant_privileges_to_account_role" "observer_platform_database" {
  account_role_name = snowflake_account_role.observer.name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.platform.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "observer_obs_schema" {
  account_role_name = snowflake_account_role.observer.name
  privileges        = ["USAGE"]

  on_schema {
    schema_name = "\"${snowflake_database.platform.name}\".\"${local.platform_schemas.obs}\""
  }

  depends_on = [snowflake_schema.platform]
}

# The 2.x provider has no event-table resource, so SELECT on it is granted by statement, with a
# revert that takes it away again. See the open point in README.md.
resource "snowflake_execute" "observer_event_table" {
  execute = "GRANT SELECT ON EVENT TABLE ${local.event_table_fqn} TO ROLE ${snowflake_account_role.observer.name}"
  revert  = "REVOKE SELECT ON EVENT TABLE ${local.event_table_fqn} FROM ROLE ${snowflake_account_role.observer.name}"
  query   = "SHOW GRANTS ON EVENT TABLE ${local.event_table_fqn}"

  depends_on = [snowflake_execute.event_table]
}
