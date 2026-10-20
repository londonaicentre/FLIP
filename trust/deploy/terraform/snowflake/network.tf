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
# Egress. A trust node makes exactly two kinds of outbound call: the hub API on 443 and the FL
# server endpoint(s) from its kit. Both are named, both are off by default, and there is no
# catch-all rule: a trial account has no external network access at all, so the trial runs hubless
# and this whole file is inert until enable_hub_egress is set.
resource "snowflake_network_rule" "hub_egress" {
  count = var.enable_hub_egress ? 1 : 0

  database   = snowflake_database.trust.name
  schema     = snowflake_schema.trust["app"].name
  name       = "${local.prefix}_HUB_EGRESS"
  type       = "HOST_PORT"
  mode       = "EGRESS"
  value_list = local.egress_hosts
  comment    = "FLIP ${local.code}: the only hosts a service may reach."

  lifecycle {
    precondition {
      condition     = length(local.egress_hosts) > 0
      error_message = "enable_hub_egress needs hub_api_host and/or fl_server_endpoints: an empty egress rule would be a silent allow-nothing."
    }
  }
}

resource "snowflake_external_access_integration" "hub" {
  count = var.enable_hub_egress ? 1 : 0

  name                  = local.hub_eai
  allowed_network_rules = [snowflake_network_rule.hub_egress[0].fully_qualified_name]
  enabled               = true
  comment               = "FLIP ${local.code}: hub and FL server egress."

  # Secrets are created by `make sync-kit` (#1582) and bound to services there, not through the
  # integration: nothing in this root may reference a secret value.
  allowed_authentication_secrets {
    none = true
  }
}

# Account sign-in allowlist. Empty by default so a first apply cannot lock the operator out; set
# admin_allowed_cidrs once the operator's egress address is known.
resource "snowflake_network_policy" "admin" {
  count = length(var.admin_allowed_cidrs) > 0 ? 1 : 0

  name            = local.network_policy
  allowed_ip_list = var.admin_allowed_cidrs
  comment         = "FLIP ${local.code}: addresses allowed to reach the account."
}

resource "snowflake_network_policy_attachment" "account" {
  count = length(var.admin_allowed_cidrs) > 0 ? 1 : 0

  network_policy_name = snowflake_network_policy.admin[0].name
  set_for_account     = true
}
