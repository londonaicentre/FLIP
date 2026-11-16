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

# The same naming contract as the platform and workload roots (epic #1589): every name derives
# from the trust code alone, so the three roots agree without sharing state.
locals {
  code   = upper(var.trust_code)
  prefix = "FLIP_${local.code}"

  roles = {
    deploy      = "${local.prefix}_DEPLOY"
    svc         = "${local.prefix}_SVC"
    omop_reader = "${local.prefix}_OMOP_READER"
    omop_loader = "${local.prefix}_OMOP_LOADER"
    observer    = "${local.prefix}_OBSERVER"
    converter   = "${local.prefix}_CONVERTER"
  }

  deploy_user = "${local.prefix}_DEPLOY_USER"
}
