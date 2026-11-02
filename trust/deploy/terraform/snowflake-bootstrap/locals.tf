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

locals {
  code   = upper(var.trust_code)
  prefix = "FLIP_${local.code}"

  # The trust roles are created by the platform root; the bootstrap only names them, for the
  # egress-integration grants (observability.tf) and the CI role's grant option.
  roles = {
    deploy = "${local.prefix}_DEPLOY"
    svc    = "${local.prefix}_SVC"
  }

  warehouse        = "${local.prefix}_WH"
  resource_monitor = "${local.prefix}_RM"
  hub_eai          = "${local.prefix}_HUB_EAI"
  network_policy   = "${local.prefix}_ADMIN_NP"
  password_policy  = "${local.prefix}_PASSWORD_POLICY"
  session_policy   = "${local.prefix}_SESSION_POLICY"

  admin_database  = "FLIP_ADMIN"
  event_table_fqn = "FLIP_PLATFORM.OBS.FLIP_EVENTS"

  # CI (ci.tf). One CI role per account, not per trust: the platform root it applies is the
  # account's.
  ci_role      = "FLIP_PLATFORM_CI"
  ci_read_role = "FLIP_PLATFORM_CI_READ"

  # Exactly what the CI role holds on the account. Never MANAGE GRANTS, CREATE INTEGRATION, CREATE
  # NETWORK POLICY, or any privilege that reaches account policy: tests/test_bootstrap_guards.py pins
  # this list.
  ci_account_privileges = [
    "CREATE ROLE",
    "CREATE USER",
    "CREATE DATABASE",
    "EXECUTE TASK",
  ]

  # The trust deploy role's account privileges, which CI holds WITH GRANT OPTION so it can pass on
  # these and no others. Must equal the platform root's local.deploy_account_privileges (pinned).
  deploy_account_privileges = [
    "CREATE DATABASE",
    "CREATE COMPUTE POOL",
    "BIND SERVICE ENDPOINT",
    "EXECUTE TASK",
  ]

  # What the platform root grants on the warehouse (DEPLOY gets all three, the others a subset).
  ci_warehouse_privileges = ["USAGE", "OPERATE", "MONITOR"]

  # Snowflake maps one OIDC subject to one user, so plan and apply trust different environments:
  # sf-<env> (protected: the apply branch only) for apply, sf-<env>-plan (any PR) for the read-only
  # plan user. A pull request's job can therefore never present the apply user's subject.
  github_oidc_subject      = "repo:${var.github_repository}:environment:${var.ci_github_environment}"
  github_oidc_plan_subject = "repo:${var.github_repository}:environment:${var.ci_github_environment}-plan"
  ci_users = {
    plan  = { name = "FLIP_CI_PLAN_USER", role = local.ci_read_role, subject = local.github_oidc_plan_subject }
    apply = { name = "FLIP_CI_APPLY_USER", role = local.ci_role, subject = local.github_oidc_subject }
  }

  egress_hosts = var.enable_hub_egress ? concat(
    var.hub_api_host == "" ? [] : ["${var.hub_api_host}:443"],
    var.fl_server_endpoints,
  ) : []
}
