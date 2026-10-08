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

# Static tflint lint over the AWS Terraform tree.
#
# `terraform validate` accepts code that declares a variable nothing reads, a
# module with no provider version constraint, or an AWS argument value the API
# would reject at apply time. tflint catches those statically: the bundled
# `terraform` ruleset (recommended preset) plus the AWS ruleset, configured in
# deploy/providers/AWS/.tflint.hcl. No cloud credentials, no terraform init, no
# plan — safe on fork PRs and under `act`. `tflint --init` downloads the AWS
# ruleset plugin from GitHub (set GITHUB_TOKEN to avoid the anonymous API rate
# limit).
#
# Every directory holding .tf files is linted on its own (`--chdir`), so a
# module is checked even where no root calls it. The two canary fixtures under
# scripts/tests/ are excluded from the real lint: checkov_canary is deliberately
# non-compliant for checkov_lint.sh, and tflint_canary exists to fail here.
#
# The harness distrusts itself before trusting a green lint: it pins and
# asserts the tflint version and requires the canary fixture to FAIL before
# linting the real tree, so a broken install or a config tflint no longer
# reads can never produce a vacuous green.
#
# A deliberate exception is acknowledged in-code with
# `# tflint-ignore: <rule_name> # <why>` on the line above the flagged block —
# never by disabling the rule in .tflint.hcl. The rationale must follow a second
# `#`: any other separator (`-- why`) makes tflint ignore the annotation.

set -euo pipefail

# Keep in sync with tflint_version in .github/workflows/validate_terraform.yml.
TFLINT_VERSION="0.64.0"

AWS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${AWS_ROOT}/.tflint.hcl"
CANARY_DIR="${AWS_ROOT}/scripts/tests/tflint_canary"

TFLINT_OVERRIDDEN="${TFLINT:+yes}"
if [ -n "${TFLINT_OVERRIDDEN}" ]; then
    : # caller-provided command wins, and skips the version assertion
elif command -v tflint > /dev/null 2>&1; then
    TFLINT="tflint"
else
    echo "ERROR: tflint not found — install v${TFLINT_VERSION} from" >&2
    echo "       https://github.com/terraform-linters/tflint/releases/tag/v${TFLINT_VERSION}" >&2
    exit 1
fi

resolved_version="$(${TFLINT} --version 2> /dev/null | sed -n 's/^TFLint version \(.*\)$/\1/p' | head -1 || true)"
echo "tflint: ${TFLINT} (version ${resolved_version:-unknown})"
if [ -z "${TFLINT_OVERRIDDEN}" ] && [ "${resolved_version}" != "${TFLINT_VERSION}" ]; then
    echo "ERROR: resolved tflint version '${resolved_version:-none}' != pinned ${TFLINT_VERSION}." >&2
    echo "       Run with TFLINT=<path to v${TFLINT_VERSION}> or align your install." >&2
    exit 1
fi

${TFLINT} --init --config "${CONFIG}"

# --- Canary: prove the lint can fail before trusting that it passes ---------
# A wrong config path, a broken plugin install, or a rule silently dropped from
# the preset would all make the real lint pass vacuously. The canary declares a
# variable nothing reads; require tflint to flag it.
canary_output="$(${TFLINT} --chdir "${CANARY_DIR}" --config "${CONFIG}" --format compact 2>&1)" || true
if ! grep -q "terraform_unused_declarations" <<< "${canary_output}"; then
    echo "ERROR: canary fixture did not fail terraform_unused_declarations — the tflint lint harness is broken." >&2
    echo "--- tflint output ---" >&2
    echo "${canary_output}" >&2
    exit 1
fi
echo "Canary OK: tflint flags the deliberately unused declaration."

# --- Real lint --------------------------------------------------------------
dirs="$(find "${AWS_ROOT}" -name '*.tf' \
    -not -path '*/.terraform/*' \
    -not -path '*/scripts/tests/checkov_canary/*' \
    -not -path '*/scripts/tests/tflint_canary/*' \
    -exec dirname {} \; | sort -u)"
if [ -z "${dirs}" ]; then
    echo "ERROR: found no Terraform directories under ${AWS_ROOT}." >&2
    exit 1
fi

failed=0
while IFS= read -r dir; do
    rel="${dir#"${AWS_ROOT}"}"
    echo "── deploy/providers/AWS${rel}"
    if ! ${TFLINT} --chdir "${dir}" --config "${CONFIG}" --format compact; then
        failed=1
    fi
done <<< "${dirs}"

if [ "${failed}" -ne 0 ]; then
    echo "ERROR: tflint found issues (above)." >&2
    exit 1
fi
echo "tflint: no issues."
