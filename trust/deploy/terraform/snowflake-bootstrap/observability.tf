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

# Ordering on a NEW account: the event table is created by the platform root (it lives in CI's
# FLIP_PLATFORM database), so the first bootstrap apply runs with set_event_table = false, then the
# first platform apply, then a second bootstrap apply with set_event_table = true. Setting the account
# parameter needs ACCOUNTADMIN plus OWNERSHIP or INSERT on the event table; ACCOUNTADMIN has the
# latter through SYSADMIN, which holds FLIP_PLATFORM_CI (ci.tf).
# Account-wide: everything logs into the platform event table. Setting an account parameter needs
# ACCOUNTADMIN, which is exactly why it lives in this tier and not in the routine apply.
#
# Note what is deliberately NOT here: no account, database or schema log-level parameter. Turning
# logging on account-wide would collect every Snowflake-internal procedure's output and bill the
# storage for it. Verbosity is set per service, in each spec's
# logExporters.eventTableConfig.logLevel, and the workload root's static guards refuse DEBUG and
# TRACE there (trust/deploy/terraform/snowflake/variables.tf, service_log_level).
resource "snowflake_account_parameter" "event_table" {
  count = var.set_event_table ? 1 : 0

  key   = "EVENT_TABLE"
  value = local.event_table_fqn

}
# USAGE on the egress integration, so a service spec may reference it. The integration itself is
# an account object the workload role can neither create nor alter.
resource "snowflake_grant_privileges_to_account_role" "deploy_eai" {
  count = var.enable_hub_egress ? 1 : 0

  account_role_name = local.roles.deploy
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "INTEGRATION"
    object_name = snowflake_external_access_integration.hub[0].name
  }
}
resource "snowflake_grant_privileges_to_account_role" "svc_eai" {
  count = var.enable_hub_egress ? 1 : 0

  account_role_name = local.roles.svc
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "INTEGRATION"
    object_name = snowflake_external_access_integration.hub[0].name
  }
}