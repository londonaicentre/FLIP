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

# Partial configuration, exactly as in the workload root: the type is fixed here and every setting
# arrives through `-backend-config` (the Makefile passes BACKEND_CONFIG). The two roots keep
# SEPARATE state files. That is the point of the split: a routine workload apply can never touch
# a role, a user, an account policy or an account grant, because those objects are not in its state.
terraform {
  backend "local" {}
}
