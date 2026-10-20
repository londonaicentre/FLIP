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
# Six account roles, no grant to PUBLIC anywhere in this root. ACCOUNTADMIN is used once, by hand,
# to create the admin role and hand it the account-level privileges this root needs (README,
# "Bootstrap"); nothing in automation assumes it.
resource "snowflake_account_role" "admin" {
  name    = local.roles.admin
  comment = "FLIP ${local.code}: owns the trust's objects. Parent of every other FLIP ${local.code} role."
}

resource "snowflake_account_role" "deploy" {
  name    = local.roles.deploy
  comment = "FLIP ${local.code}: deploy identity. Creates and operates services, never reads clinical data."
}

resource "snowflake_account_role" "svc" {
  name    = local.roles.svc
  comment = "FLIP ${local.code}: the role SPCS services run as."
}

resource "snowflake_account_role" "omop_reader" {
  name    = local.roles.omop_reader
  comment = "FLIP ${local.code}: SELECT-only on the OMOP schema."
}

resource "snowflake_account_role" "omop_loader" {
  name    = local.roles.omop_loader
  comment = "FLIP ${local.code}: loads the OMOP tables (#1580)."
}

resource "snowflake_account_role" "observer" {
  name    = local.roles.observer
  comment = "FLIP ${local.code}: read-only on the event table views and object metadata."
}

# Hierarchy. Every role rolls up to the trust admin role, and the trust admin role to SYSADMIN, so
# an account admin can see and clean up the tree. Nothing rolls up to the deploy role except the
# service role it has to create services for.
resource "snowflake_grant_account_role" "svc_to_deploy" {
  role_name        = snowflake_account_role.svc.name
  parent_role_name = snowflake_account_role.deploy.name
}

resource "snowflake_grant_account_role" "omop_reader_to_svc" {
  role_name        = snowflake_account_role.omop_reader.name
  parent_role_name = snowflake_account_role.svc.name
}

resource "snowflake_grant_account_role" "to_admin" {
  for_each = {
    deploy      = snowflake_account_role.deploy.name
    omop_loader = snowflake_account_role.omop_loader.name
    observer    = snowflake_account_role.observer.name
  }
  role_name        = each.value
  parent_role_name = snowflake_account_role.admin.name
}

resource "snowflake_grant_account_role" "admin_to_sysadmin" {
  role_name        = snowflake_account_role.admin.name
  parent_role_name = "SYSADMIN"
}
