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

# The WORKLOAD tier, applied routinely as FLIP_<CODE>_DEPLOY_USER (TYPE = SERVICE) with key-pair
# auth. The role it assumes holds four account privileges and nothing more; the account's security
# objects live in ../snowflake-platform and are out of this root's reach.
#
# No credential is ever written here or into a variable: the provider reads the deploy identity
# from the environment (SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY (PEM contents),
# SNOWFLAKE_AUTHENTICATOR=SNOWFLAKE_JWT) or from ~/.snowflake/connections.toml. A private key in a
# Terraform variable would land in state and in every plan output.
provider "snowflake" {
  organization_name = var.organization_name
  account_name      = var.account_name
  role              = var.terraform_role
  warehouse         = var.terraform_warehouse

  # stage_internal, compute_pool and image_repository are stable as of provider 2.22. The one
  # preview resource is the converter's launch procedure (converter.tf, FLIP#1578); any other
  # preview resource must be listed here explicitly too.
  preview_features_enabled = ["snowflake_procedure_sql_resource"]
}
