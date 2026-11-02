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

# The CI role (epic #1589, step 2). The platform root is applied by CI as FLIP_PLATFORM_CI, and this
# file is what contains it. It may create roles, users and databases, and it may grant only what it
# owns (the objects it created) plus the privileges below that carry a grant option. It never holds
# MANAGE GRANTS, CREATE INTEGRATION, any system role, or ownership of anything it did not create, so
# it cannot reach ACCOUNTADMIN, SECURITYADMIN, account policies or another team's objects.
#
# Residual risk, said plainly: CREATE USER plus owning the FLIP roles means CI can mint a user that
# holds any FLIP role. That is inherent to CI applying the platform tier; it stops at FLIP's roles,
# and every user it creates appears in the plan comment and in drift.

resource "snowflake_account_role" "platform_ci" {
  name    = local.ci_role
  comment = "FLIP platform: the role CI applies the platform root as (epic #1589). Contained by snowflake-bootstrap."
}

# Break-glass visibility: ACCOUNTADMIN inherits SYSADMIN, so it can see and repair what CI owns,
# and it needs that ownership-derived access to set EVENT_TABLE (observability.tf).
resource "snowflake_grant_account_role" "platform_ci_to_sysadmin" {
  role_name        = snowflake_account_role.platform_ci.name
  parent_role_name = "SYSADMIN"
}

resource "snowflake_grant_privileges_to_account_role" "ci_account" {
  account_role_name = snowflake_account_role.platform_ci.name
  privileges        = local.ci_account_privileges
  on_account        = true
}

# The trust roles' account privileges, held with the grant option so the platform root can pass on
# exactly these and no other account privilege.
resource "snowflake_grant_privileges_to_account_role" "ci_account_grantable" {
  account_role_name = snowflake_account_role.platform_ci.name
  privileges        = local.deploy_account_privileges
  with_grant_option = true
  on_account        = true
}

# The cost views read SNOWFLAKE.ACCOUNT_USAGE; a view's owner must be able to read what it selects.
resource "snowflake_grant_privileges_to_account_role" "ci_account_usage" {
  account_role_name = snowflake_account_role.platform_ci.name
  privileges        = ["IMPORTED PRIVILEGES"]

  on_account_object {
    object_type = "DATABASE"
    object_name = "SNOWFLAKE"
  }
}

# Read-only role for PR plans and drift: sees metadata, creates nothing.
resource "snowflake_account_role" "platform_ci_read" {
  name    = local.ci_read_role
  comment = "FLIP platform: read-only role CI plans the platform root as (epic #1589)."
}

resource "snowflake_grant_account_role" "platform_ci_read_to_sysadmin" {
  role_name        = snowflake_account_role.platform_ci_read.name
  parent_role_name = "SYSADMIN"
}

# The CI users. No password, no key: each logs in only with a GitHub OIDC token whose subject is the
# account's GitHub environment. Plan and apply are separate users so a pull request's plan job can
# never assume the apply role.
resource "snowflake_service_user" "ci" {
  for_each = local.ci_users

  name         = each.value.name
  comment      = "FLIP platform: ${each.key} identity of the Snowflake trust CI (epic #1589); GitHub OIDC only."
  display_name = each.value.name
  default_role = each.value.role

  default_workload_identity {
    oidc {
      issuer  = "https://token.actions.githubusercontent.com"
      subject = each.value.subject
    }
  }
}

resource "snowflake_grant_account_role" "ci_to_user" {
  for_each = local.ci_users

  role_name = each.value.role
  user_name = snowflake_service_user.ci[each.key].name
}
