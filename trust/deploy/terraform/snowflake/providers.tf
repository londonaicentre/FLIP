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

# No credential is ever written here or into a variable: the provider reads the deploy identity
# from the environment (SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY or SNOWFLAKE_PRIVATE_KEY_PATH,
# SNOWFLAKE_AUTHENTICATOR=SNOWFLAKE_JWT) or from ~/.snowflake/connections.toml. A private key in a
# Terraform variable would land in state and in every plan output.
provider "snowflake" {
  organization_name = var.organization_name
  account_name      = var.account_name
  role              = var.terraform_role
  warehouse         = var.terraform_warehouse

  # Resources still marked preview in the 2.x provider. Listed explicitly so an upgrade that
  # promotes or renames one fails loudly here rather than silently changing behaviour.
  preview_features_enabled = [
    "snowflake_stage_internal_resource",
    "snowflake_compute_pool_resource",
    "snowflake_image_repository_resource",
    "snowflake_network_rule_resource",
    "snowflake_external_access_integration_resource",
    "snowflake_password_policy_resource",
    "snowflake_session_policy_resource",
    "snowflake_account_password_policy_attachment_resource",
    "snowflake_account_session_policy_attachment_resource",
    "snowflake_network_policy_attachment_resource",
  ]
}
