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
# One XSMALL warehouse. It serves the OMOP reads and the nightly task; SPCS compute is billed by
# the pools, not here.
resource "snowflake_warehouse" "trust" {
  name           = local.warehouse
  comment        = "FLIP ${local.code}: trust warehouse."
  warehouse_size = "XSMALL"
  warehouse_type = "STANDARD"

  auto_suspend        = var.warehouse_auto_suspend_secs
  auto_resume         = "true"
  initially_suspended = true

  min_cluster_count = 1
  max_cluster_count = 1

  statement_timeout_in_seconds        = var.warehouse_statement_timeout_secs
  statement_queued_timeout_in_seconds = var.warehouse_statement_timeout_secs
  # The resource monitor is an account object created by the platform tier. Attaching it needs
  # MODIFY on the monitor, which that tier grants to the deploy role; nothing here can create or
  # alter a monitor.
  resource_monitor = var.attach_resource_monitor ? local.resource_monitor : null
}
