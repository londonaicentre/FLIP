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

# Every name in BOTH roots comes from the shared contract in the epic (#1589) and is derived from
# the trust code alone, so the platform root and the workload root agree without sharing state.
# There is deliberately no remote-state data source between them.
locals {
  code   = upper(var.trust_code)
  prefix = "FLIP_${local.code}"

  # Six roles. FLIP_<CODE>_ADMIN is retired (two-tier decision, 2026-10-20, epic #1589): the
  # privileges it used to hold are split between this break-glass root and object ownership held
  # by the deploy role.
  roles = {
    deploy      = "${local.prefix}_DEPLOY"
    svc         = "${local.prefix}_SVC"
    omop_reader = "${local.prefix}_OMOP_READER"
    omop_loader = "${local.prefix}_OMOP_LOADER"
    observer    = "${local.prefix}_OBSERVER"
    converter   = "${local.prefix}_CONVERTER"
  }

  deploy_user      = "${local.prefix}_DEPLOY_USER"
  hub_eai          = "${local.prefix}_HUB_EAI"
  resource_monitor = "${local.prefix}_RM"
  network_policy   = "${local.prefix}_ADMIN_NP"
  password_policy  = "${local.prefix}_PASSWORD_POLICY"
  session_policy   = "${local.prefix}_SESSION_POLICY"

  # The platform tier owns one small database. An event table needs a database, and putting it in
  # the trust database would make the workload root's destroy take the account's logging with it.
  platform_database = "FLIP_PLATFORM"
  platform_schemas = {
    obs      = "OBS"
    policies = "POLICIES"
  }
  event_table     = "FLIP_EVENTS"
  event_table_fqn = "${local.platform_database}.${local.platform_schemas.obs}.${local.event_table}"

  # The retention task (#1584). Named for the platform, not for a trust: one event table serves
  # every trust in the account, so its pruning is not per-trust work.
  prune_task = "FLIP_PLATFORM_PRUNE_EVENTS"

  # The observability views (#1584). Every one of them lives in FLIP_PLATFORM.OBS and is readable
  # by FLIP_<CODE>_OBSERVER alone; the dashboard in dashboards/ queries these names and nothing
  # else, so a tile never has to know the event table's shape.
  obs_views = {
    events            = "V_EVENTS"
    service_status    = "V_SERVICE_STATUS"
    service_restarts  = "V_SERVICE_RESTARTS"
    recent_errors     = "V_RECENT_ERRORS"
    fl_client_logs    = "V_FL_CLIENT_LOGS"
    compute_pool_cost = "V_COMPUTE_POOL_COST"
    warehouse_cost    = "V_WAREHOUSE_COST"
  }

  # How an fl-client service is recognised in the event table. The #1582 naming contract gives
  # every FL client service a name beginning FL_CLIENT (one per FL network, FL_CLIENT_NET_<n>).
  fl_client_service_pattern = "FL_CLIENT%"

  # The account privileges the workload role is allowed to hold, and nothing else. No CREATE ROLE,
  # no CREATE USER, no CREATE INTEGRATION, no CREATE NETWORK POLICY, no MANAGE GRANTS, and no
  # CREATE WAREHOUSE: the warehouse is platform-owned so its resource monitor cannot be shed.
  # EXECUTE TASK lets the role run the tasks it owns, i.e. the nightly compute-pool suspend.
  deploy_account_privileges = [
    "CREATE DATABASE",
    "CREATE COMPUTE POOL",
    "BIND SERVICE ENDPOINT",
    "EXECUTE TASK",
  ]

  warehouse = "${local.prefix}_WH"

  # Egress is a closed list: the hub API on 443 and the FL server endpoints from the kit.
  egress_hosts = var.enable_hub_egress ? concat(
    var.hub_api_host == "" ? [] : ["${var.hub_api_host}:443"],
    var.fl_server_endpoints,
  ) : []
}
