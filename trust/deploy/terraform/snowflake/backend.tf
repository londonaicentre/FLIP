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

# The backend is a partial configuration: the type is fixed here, every setting comes from
# `-backend-config` (the Makefile passes BACKEND_CONFIG). The test account keeps state on the
# operator machine, encrypted by OpenTofu state encryption (see encryption.tf.example), because
# the account has no object storage of its own and the hub's state is deliberately out of reach.
#
# Production: a site chooses its own remote backend and drops a `backend_override.tf` beside this
# file (gitignored, read by OpenTofu after this one) declaring, for example, `backend "s3" {}` or
# `backend "azurerm" {}`, then re-runs `make init BACKEND_CONFIG=...`. See README.md.
terraform {
  backend "local" {}
}
