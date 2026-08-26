#!/usr/bin/env bash
#
# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

# Static IAM policy-content lint over the AWS Terraform tree (FLIP#1052).
#
# `terraform fmt`/`validate` only check syntax and schema — nothing stops an
# overly-broad IAM statement (wildcard Resource/Action on data-access actions)
# from merging silently. This script runs checkov's IAM policy checks over
# deploy/providers/AWS statically: no cloud credentials, no terraform init, no
# plan — safe on fork PRs and under `act`.
#
# Both IAM policy syntaxes in this tree are covered: checkov evaluates
# `data "aws_iam_policy_document"` HCL blocks (the DOCUMENT_CHECKS set) as well
# as `jsonencode()` policies on aws_iam_policy/aws_iam_role_policy (the
# RESOURCE_CHECKS set). Checkov also knows which AWS actions support no
# resource-level scoping (e.g. ssmmessages:*, ec2:Describe*), so the deliberate
# `resources = ["*"]` statements on those actions pass without suppression.
#
# Deliberate breadth on a restrictable action is acknowledged in-code, never by
# weakening this check list: put `# checkov:skip=<CHECK_ID>:<rationale>` inside
# the flagged resource/data block. The rationale is mandatory.
#
# Known limitation: these stock checks only flag a literal "*" Resource/Action.
# An interpolated bucket-root grant (e.g. "${aws_s3_bucket.x.arn}/*" on
# s3:GetObject) still needs human review.
#
# Usage:
#     bash deploy/providers/AWS/scripts/iam_policy_lint.sh
# The checkov command can be overridden, e.g. CHECKOV="uvx checkov==3.3.14".

set -euo pipefail

# Keep in sync with the `pip install checkov==...` pin in
# .github/workflows/validate_terraform.yml.
CHECKOV_VERSION="3.3.14"

AWS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANARY_DIR="${AWS_ROOT}/scripts/tests/iam_lint_canary"

if [ -n "${CHECKOV:-}" ]; then
    : # caller-provided command wins
elif command -v checkov > /dev/null 2>&1; then
    CHECKOV="checkov"
elif command -v uvx > /dev/null 2>&1; then
    CHECKOV="uvx checkov==${CHECKOV_VERSION}"
else
    echo "ERROR: checkov not found — install it (pip install checkov==${CHECKOV_VERSION}) or install uv." >&2
    exit 1
fi

# Checks that evaluate `data "aws_iam_policy_document"` blocks.
DOCUMENT_CHECKS="CKV_AWS_1,CKV_AWS_49,CKV_AWS_107,CKV_AWS_108,CKV_AWS_109,CKV_AWS_110,CKV_AWS_111,CKV_AWS_283,CKV_AWS_356"
# Their counterparts for jsonencode()/JSON policies on aws_iam_policy & co.
RESOURCE_CHECKS="CKV_AWS_62,CKV_AWS_63,CKV_AWS_286,CKV_AWS_287,CKV_AWS_288,CKV_AWS_289,CKV_AWS_290,CKV_AWS_355,CKV2_AWS_40"
IAM_CHECKS="${DOCUMENT_CHECKS},${RESOURCE_CHECKS}"

# --- Canary: prove the scan can fail before trusting that it passes ---------
# A wrong directory, a broken install, or a checkov behaviour change would all
# make the real scan pass vacuously. The canary fixture holds a deliberately
# broad policy document; if checkov reports no failure on it, this harness is
# broken and the green result below would be meaningless.
canary_output="$(${CHECKOV} -d "${CANARY_DIR}" --framework terraform \
    --check "${IAM_CHECKS}" --skip-download --compact --quiet 2>&1)" || true
if ! grep -q "FAILED for resource" <<< "${canary_output}"; then
    echo "ERROR: canary fixture produced no findings — the IAM lint harness is broken." >&2
    echo "--- checkov output ---" >&2
    echo "${canary_output}" >&2
    exit 1
fi
echo "Canary OK: checkov flags the deliberately broad fixture."

# --- Real scan --------------------------------------------------------------
echo "Scanning ${AWS_ROOT} with checks: ${IAM_CHECKS}"
${CHECKOV} -d "${AWS_ROOT}" --framework terraform \
    --check "${IAM_CHECKS}" \
    --skip-path "scripts/tests/iam_lint_canary" \
    --skip-download --compact --quiet
