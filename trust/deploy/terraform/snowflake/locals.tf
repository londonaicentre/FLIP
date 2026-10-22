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

# Every name in this root comes from here, and from nowhere else, so this root, the platform root
# (../snowflake-platform) and the service specs in #1582 derive the same names from the same trust
# code. The contract is recorded in the epic (#1589).
#
# The role names below are NOT resources here: the platform tier creates them, and this root only
# names them in grants on objects it owns. There is no remote-state data source between the two
# roots: convention is the whole coupling.
locals {
  code   = upper(var.trust_code)
  prefix = "FLIP_${local.code}"

  database = "FLIP_TRUST_${local.code}"
  schemas = {
    app    = "APP"
    omop   = "OMOP"
    stages = "STAGES"
    obs    = "OBS"
  }

  # FLIP_<CODE>_ADMIN is retired (two-tier decision, 2026-10-20, epic #1589).
  roles = {
    deploy      = "${local.prefix}_DEPLOY"
    svc         = "${local.prefix}_SVC"
    omop_reader = "${local.prefix}_OMOP_READER"
    omop_loader = "${local.prefix}_OMOP_LOADER"
    observer    = "${local.prefix}_OBSERVER"
  }

  deploy_user      = "${local.prefix}_DEPLOY_USER"
  warehouse        = "${local.prefix}_WH"
  pool             = "${local.prefix}_POOL"
  job_pool         = "${local.prefix}_JOB_POOL"
  gpu_pool         = "${local.prefix}_GPU_POOL"
  hub_eai          = "${local.prefix}_HUB_EAI"
  suspend_task     = "${local.prefix}_SUSPEND_POOLS"
  image_repository = "IMAGES"

  # The STAGES schema, one stage per concern so a service mounts only what it needs (#1589).
  stage_names = [
    "FL_KIT",
    "SEED",
    "XNAT_ARCHIVE",
    "IMAGES",
    "CONVERTER_IO",
    "GOVERNANCE",
    "COHORT_SNAPSHOTS",
  ]

  # Secrets are created by `make sync-kit` (#1582), never here. Terraform only declares the future
  # grants that will cover them.
  fl_nets = [for n in range(var.num_fl_nets) : n + 1]

  pools = merge(
    {
      (local.pool)     = var.pool_instance_family
      (local.job_pool) = var.job_pool_instance_family
    },
    var.enable_gpu_pool ? { (local.gpu_pool) = var.gpu_pool_instance_family } : {}
  )
}
