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

# The break-glass tier. This root is applied by a PERSON user holding ACCOUNTADMIN (or
# SECURITYADMIN where that is enough), with MFA, and only when the account's security objects
# really have to change.
#
# No credential is ever written here or into a variable. The provider reads them from the
# environment:
#
#   export SNOWFLAKE_ORGANIZATION_NAME=...   # or organization_name in the tfvars file
#   export SNOWFLAKE_ACCOUNT_NAME=...
#   export SNOWFLAKE_USER=jane.doe
#   export SNOWFLAKE_PASSWORD=...            # read from the password manager, never typed into a file
#   export SNOWFLAKE_PASSCODE=...            # the current TOTP code, when authenticator is username_password_mfa
#
# `externalbrowser` is the alternative for an account federated to an IdP, and needs no password in
# the environment at all. Nothing here accepts a credential as a Terraform variable: a password or
# a passcode in a variable would land in state and in every plan file.
provider "snowflake" {
  organization_name = var.organization_name
  account_name      = var.account_name
  role              = var.platform_role
  warehouse         = var.platform_warehouse
  authenticator     = var.authenticator

  # Resources still marked preview in the 2.x provider. Listed explicitly so an upgrade that
  # promotes or renames one fails loudly here rather than silently changing behaviour.
  preview_features_enabled = [
    "snowflake_network_rule_resource",
    "snowflake_external_access_integration_resource",
    "snowflake_password_policy_resource",
    "snowflake_session_policy_resource",
    "snowflake_account_password_policy_attachment_resource",
    "snowflake_account_session_policy_attachment_resource",
    "snowflake_network_policy_attachment_resource",
  ]
}
