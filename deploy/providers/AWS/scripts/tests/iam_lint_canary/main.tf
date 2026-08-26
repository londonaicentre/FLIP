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

# Canary fixture for scripts/iam_policy_lint.sh (FLIP#1052) — NOT deployed
# infrastructure. This statement is deliberately overly broad: a data-access
# action with a wildcard Resource. The lint harness asserts checkov FAILS it
# before scanning the real tree, so a broken install or an ineffective check
# list can never produce a vacuous green. No module references this directory,
# so `terraform validate` of the roots never loads it.
data "aws_iam_policy_document" "iam_lint_canary" {
  statement {
    sid       = "CanaryOverlyBroadRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["*"]
  }
}
