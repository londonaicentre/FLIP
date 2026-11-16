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

# Five account roles, created here and nowhere else. The workload root cannot create a role: it
# holds no CREATE ROLE on the account (grants.tf), which the static guards in both roots pin.
resource "snowflake_account_role" "deploy" {
  name    = local.roles.deploy
  comment = "FLIP ${local.code}: the least-privilege workload role. Owns the trust database, warehouse, pools, repository, stages and services."
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
  comment = "FLIP ${local.code}: read-only on the event table and on object metadata."
}

# How the platform tier hands the roles to the workload tier.
#
# The workload role owns the objects it creates, and an owner may grant privileges on its own
# objects without MANAGE GRANTS. What it cannot do is name a role it does not hold. So the four
# consumer roles are granted TO the deploy role here, once, and from then on a routine workload
# apply can say "GRANT SELECT ON ... TO ROLE FLIP_<CODE>_OMOP_READER" entirely within its own
# privileges. Ownership of every object stays with the deploy role.
# Owns the converter launch procedures and the dcm2niix jobs they start (FLIP#1578). A service's
# OAuth token carries its OWNER's role and SPCS cannot transfer a service's ownership, so whoever
# runs EXECUTE JOB SERVICE decides the converter container's token. This role is that owner: it
# holds CREATE SERVICE on the APP schema (job launch needs it) and READ on the archive, nothing else.
# No service or user holds it; SVC reaches it only through the fixed-shape procedures.
resource "snowflake_account_role" "converter" {
  name    = local.roles.converter
  comment = "FLIP ${local.code}: owns the dcm2niix launch procedures and their jobs (FLIP#1578)."
}

resource "snowflake_grant_account_role" "to_deploy" {
  for_each = {
    svc         = snowflake_account_role.svc.name
    omop_reader = snowflake_account_role.omop_reader.name
    omop_loader = snowflake_account_role.omop_loader.name
    observer    = snowflake_account_role.observer.name
    converter   = snowflake_account_role.converter.name
  }

  role_name        = each.value
  parent_role_name = snowflake_account_role.deploy.name
}

# A service reads OMOP through the service role.
resource "snowflake_grant_account_role" "omop_reader_to_svc" {
  role_name        = snowflake_account_role.omop_reader.name
  parent_role_name = snowflake_account_role.svc.name
}

# The tree rolls up to SYSADMIN so an account admin can see and clean it up.
resource "snowflake_grant_account_role" "deploy_to_sysadmin" {
  role_name        = snowflake_account_role.deploy.name
  parent_role_name = "SYSADMIN"
}
