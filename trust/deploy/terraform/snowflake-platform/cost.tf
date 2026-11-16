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

# A resource monitor caps WAREHOUSE credits. It does NOT cover SPCS compute pools: those are
# capped by mandatory pool auto-suspend and the nightly suspend task, both in the workload root.
#
# Creating a resource monitor needs ACCOUNTADMIN, so it lives here. The workload root attaches it
# to the warehouse it creates, using the MODIFY grant in grants.tf.
resource "snowflake_resource_monitor" "trust" {
  name         = local.resource_monitor
  credit_quota = var.credit_quota

  frequency       = "MONTHLY"
  start_timestamp = var.resource_monitor_start_timestamp

  notify_triggers           = var.credit_notify_triggers
  suspend_trigger           = 95
  suspend_immediate_trigger = 100
}
