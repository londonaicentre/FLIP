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

# The deploy identity is a SERVICE user: key-pair only, no password field, no MFA prompt and no
# human login. Only the public half is an input; the private key lives on the operator machine or
# in the CI secret store and never reaches Terraform state.
#
# Person users are deliberately NOT managed here. The break-glass operator is a person user with
# MFA created by hand, and the README asks for a second emergency ACCOUNTADMIN person user with
# separate MFA and offline credentials, so a lost authenticator is not a lost account.
resource "snowflake_service_user" "deploy" {
  name         = local.deploy_user
  comment      = "FLIP ${local.code}: deploy identity for the workload root and trust/deploy/snowflake (#1582)."
  display_name = local.deploy_user

  default_role      = snowflake_account_role.deploy.name
  default_warehouse = "${local.prefix}_WH"
  default_namespace = "FLIP_TRUST_${local.code}.APP"

  rsa_public_key = var.deploy_user_public_key

  statement_timeout_in_seconds = var.deploy_statement_timeout_secs
}

resource "snowflake_grant_account_role" "deploy_to_user" {
  role_name = snowflake_account_role.deploy.name
  user_name = snowflake_service_user.deploy.name
}
