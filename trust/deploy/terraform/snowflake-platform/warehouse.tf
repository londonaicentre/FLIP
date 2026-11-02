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

resource "snowflake_grant_privileges_to_account_role" "deploy_warehouse" {
  account_role_name = snowflake_account_role.deploy.name
  privileges        = ["USAGE", "OPERATE", "MONITOR"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = local.warehouse
  }
}

# Services and the OMOP loader run queries; the reader role is inherited through SVC.
resource "snowflake_grant_privileges_to_account_role" "warehouse_usage" {
  for_each          = toset([snowflake_account_role.svc.name, snowflake_account_role.omop_loader.name])
  account_role_name = each.value
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = local.warehouse
  }
}

resource "snowflake_grant_privileges_to_account_role" "observer_warehouse" {
  account_role_name = snowflake_account_role.observer.name
  privileges        = ["MONITOR"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = local.warehouse
  }
}
