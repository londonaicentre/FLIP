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

# Canary fixture for scripts/tflint_lint.sh — NOT deployed infrastructure. It
# declares a variable nothing reads; the lint harness asserts tflint flags it
# (terraform_unused_declarations) before linting the real tree, so a broken
# install or config can never produce a vacuous green. No module references this
# directory, and it creates no resources, so checkov has nothing to check here.

terraform {
  required_version = ">= 1.13.1"
}

variable "tflint_canary_unused" {
  description = "Deliberately unused."
  type        = string
  default     = ""
}
