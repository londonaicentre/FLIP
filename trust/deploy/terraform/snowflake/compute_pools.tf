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
# SPCS bills every node a pool holds while it runs, and resource monitors do not cover compute
# pools, so auto-suspend is mandatory here (the variable refuses 0) and a nightly task suspends
# whatever is still up (cost.tf).
resource "snowflake_compute_pool" "trust" {
  for_each = local.pools

  name              = each.key
  instance_family   = each.value
  min_nodes         = 1
  max_nodes         = each.key == local.pool ? var.pool_max_nodes : (each.key == local.job_pool ? var.job_pool_max_nodes : var.gpu_pool_max_nodes)
  auto_resume       = "true"
  auto_suspend_secs = var.pool_auto_suspend_secs

  # Created idle: an apply never starts billing by itself.
  initially_suspended = "true"

  comment = "FLIP ${local.code}: ${each.key}."
}
