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

# Static checkov security lint over the AWS Terraform tree (FLIP#1052 + the
# FLIP#1058 triage).
#
# `terraform fmt`/`validate` only check syntax and schema — nothing stops an
# overly-broad IAM statement or a hardening regression from merging silently.
# This script runs a curated, PROMOTED subset of checkov's checks over
# deploy/providers/AWS statically: no cloud credentials, no terraform init, no
# plan — safe on fork PRs and under `act`.
#
# IAM policy content (FLIP#1052) — both syntaxes are covered: checkov evaluates
# `data "aws_iam_policy_document"` HCL blocks as well as `jsonencode()` policies
# on aws_iam_policy/aws_iam_role_policy. Checkov also knows which AWS actions
# support no resource-level scoping (e.g. ssmmessages:*, ec2:Describe*), so the
# deliberate `resources = ["*"]` statements on those actions pass without
# suppression.
#
# Deliberate breadth or posture is acknowledged in-code, never by weakening
# this check list: put `# checkov:skip=<CHECK_ID>:<rationale>` inside the
# flagged resource/data block. The rationale is mandatory — enforced below.
#
# The harness distrusts itself before trusting a green scan: it pins and
# asserts the checkov version, validates every promoted check ID against
# `checkov --list` (checkov silently ignores unknown IDs), and asserts a
# deliberately broad canary fixture still FAILS — one live check per promoted
# family — before scanning the real tree.
#
# Known limitations: the IAM checks only flag a literal "*" Resource/Action —
# an interpolated bucket-root grant (e.g. "${aws_s3_bucket.x.arn}/*" on
# s3:GetObject) still needs human review. Registry-module internals are not
# scanned (--skip-download keeps the run offline and fork-safe).
#
# Usage:
#     bash deploy/providers/AWS/scripts/checkov_lint.sh
# The checkov command can be overridden, e.g. CHECKOV="uvx checkov==3.3.15" —
# an explicit override also opts out of the version assertion.

set -euo pipefail

# Keep in sync with the `pip install checkov==...` pin in
# .github/workflows/validate_terraform.yml (the version assertion below turns
# a mismatch into a loud failure rather than silent drift).
CHECKOV_VERSION="3.3.14"

AWS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANARY_DIR="${AWS_ROOT}/scripts/tests/checkov_canary"

CHECKOV_OVERRIDDEN="${CHECKOV:+yes}"
if [ -n "${CHECKOV_OVERRIDDEN}" ]; then
    : # caller-provided command wins, and skips the version assertion
elif command -v checkov > /dev/null 2>&1; then
    CHECKOV="checkov"
elif command -v uvx > /dev/null 2>&1; then
    CHECKOV="uvx checkov==${CHECKOV_VERSION}"
else
    echo "ERROR: checkov not found — install it (pip install checkov==${CHECKOV_VERSION}) or install uv." >&2
    exit 1
fi

# A `checkov` from PATH may be any version, and check IDs move between
# releases — assert the pin so local runs cannot silently diverge from CI.
resolved_version="$(${CHECKOV} --version 2> /dev/null | tail -1 || true)"
echo "checkov: ${CHECKOV} (version ${resolved_version:-unknown})"
if [ -z "${CHECKOV_OVERRIDDEN}" ] && [ "${resolved_version}" != "${CHECKOV_VERSION}" ]; then
    echo "ERROR: resolved checkov version '${resolved_version:-none}' != pinned ${CHECKOV_VERSION}." >&2
    echo "       Run with CHECKOV=\"uvx checkov==${CHECKOV_VERSION}\" or align your install." >&2
    exit 1
fi

# --- Promoted check list ----------------------------------------------------
# IAM policy content (FLIP#1052).
# Checks that evaluate `data "aws_iam_policy_document"` blocks:
IAM_DOCUMENT_CHECKS="CKV_AWS_1,CKV_AWS_49,CKV_AWS_107,CKV_AWS_108,CKV_AWS_109,CKV_AWS_110,CKV_AWS_111,CKV_AWS_283,CKV_AWS_356"
# Their counterparts for jsonencode()/JSON policies on aws_iam_policy & co.:
IAM_RESOURCE_CHECKS="CKV_AWS_62,CKV_AWS_63,CKV_AWS_286,CKV_AWS_287,CKV_AWS_288,CKV_AWS_289,CKV_AWS_290,CKV_AWS_355,CKV2_AWS_40"

# Infrastructure-posture checks promoted by the FLIP#1058 triage. Each is
# either FIXED in the tree or ACCEPTED with an inline `checkov:skip` +
# rationale at the resource, so a new unjustified instance turns the lint red:
#   CKV_AWS_79   IMDSv1 disabled on EC2 (fixed: metadata_options everywhere)
#   CKV_TF_2     registry modules carry a version constraint (fixed: alb/nlb)
#   CKV_AWS_259  HSTS on CloudFront response-header policies (skips: preload
#                deliberately withheld; the header itself IS sent)
#   CKV_AWS_192 / CKV2_AWS_47  WAF Log4j managed rule (skips: rule present,
#                count-mode rollout is deliberate)
#   CKV2_AWS_34  SSM parameters unencrypted (skips: non-secret by design,
#                some read cross-account where an AWS-managed CMK can't reach)
#   CKV2_AWS_64  KMS key policy undefined (skip: default policy + IAM grants
#                is the documented design)
POSTURE_CHECKS="CKV_AWS_79,CKV_TF_2,CKV_AWS_259,CKV_AWS_192,CKV2_AWS_47,CKV2_AWS_34,CKV2_AWS_64"

CHECKS="${IAM_DOCUMENT_CHECKS},${IAM_RESOURCE_CHECKS},${POSTURE_CHECKS}"

# --- Classes triaged in FLIP#1058 and deliberately NOT promoted -------------
# (accepted repo-wide; re-open FLIP#1058 or file a new issue to revisit):
#   CKV_TF_1                     registry modules pinned by version, not git
#                                SHA — HashiCorp registry + version constraint
#                                is the accepted supply-chain posture
#   CKV_AWS_158 / CKV_AWS_338    log-group CMK/1y-retention — CMK contradicts
#                                the deliberate default KMS key policy (kms.tf)
#                                and retention is a cost/ops choice
#   CKV_AWS_144/145/21/18/300,
#   CKV2_AWS_61/62/65            per-bucket S3 posture (replication, KMS-by-
#                                default, versioning, logging, lifecycle,
#                                notifications, ACL config) — cost/product
#                                decisions, not silent regressions
#   CKV_AWS_382                  egress-all SGs — deliberate outbound-only
#                                pattern (trusts poll out; no inbound)
#   CKV_AWS_126/135              EC2 detailed monitoring / EBS optimisation
#   CKV_AWS_50/115/117/173/272   sg-drift Lambda hardening (X-Ray, concurrency,
#                                VPC, env-var CMK, code signing)
#   CKV_AWS_26/27                sg-drift SNS/SQS CMK — alert plumbing, no
#                                sensitive payload; EventBridge+CMK coupling
#   CKV_AWS_310/374              CloudFront origin failover / geo restriction
#   CKV_AWS_378                  ALB target group HTTP — TLS terminates at the
#                                edge; targets are private-subnet ECS tasks
#   CKV_AWS_184                  EFS CMK — AWS-managed encryption at rest
#   CKV2_AWS_5                   "SG not attached" — graph false positives for
#                                SGs attached through module outputs

# --- Guard: every promoted ID must exist in this checkov --------------------
# Checkov drops unknown IDs from --check SILENTLY (verified on 3.3.14), so a
# typo or an upstream rename would narrow the gate forever with zero signal.
available_checks="$(${CHECKOV} --list 2> /dev/null || true)"
for check_id in ${CHECKS//,/ }; do
    if ! grep -qw "${check_id}" <<< "${available_checks}"; then
        echo "ERROR: promoted check ${check_id} is unknown to checkov ${CHECKOV_VERSION} — typo or version drift;" >&2
        echo "       checkov would silently ignore it, so the gate would narrow with no signal." >&2
        exit 1
    fi
done

# --- Guard: every checkov:skip must carry a rationale -----------------------
# Checkov itself accepts a bare `# checkov:skip=<ID>` (verified) — enforce the
# mandatory-rationale rule here instead.
bare_skips="$(grep -rEn '#[[:space:]]*checkov:skip=[A-Za-z0-9_]+[[:space:]]*(:[[:space:]]*)?$' \
    --include='*.tf' "${AWS_ROOT}" || true)"
if [ -n "${bare_skips}" ]; then
    echo "ERROR: checkov:skip without a rationale — the format is # checkov:skip=<ID>:<why this is deliberate>:" >&2
    echo "${bare_skips}" >&2
    exit 1
fi

# --- Canary: prove the scan can fail before trusting that it passes ---------
# A wrong directory, a broken install, or a checkov behaviour change would all
# make the real scan pass vacuously. The canary fixture holds one deliberate
# violation per promoted family (IAM document + posture); require checkov to
# FAIL both, so a green here certifies the harness end to end.
canary_output="$(${CHECKOV} -d "${CANARY_DIR}" --framework terraform \
    --check "${CHECKS}" --skip-download --compact --quiet 2>&1)" || true
for must_fail in CKV_AWS_356 CKV_AWS_79; do
    if ! grep -q "${must_fail}" <<< "${canary_output}"; then
        echo "ERROR: canary fixture did not fail ${must_fail} — the checkov lint harness is broken." >&2
        echo "--- checkov output ---" >&2
        echo "${canary_output}" >&2
        exit 1
    fi
done
echo "Canary OK: checkov flags the deliberately broad fixture (both check families live)."

# --- Real scan --------------------------------------------------------------
echo "Scanning ${AWS_ROOT} with checks: ${CHECKS}"
# NB --skip-path is an UNANCHORED regex over file paths — keep the trailing
# slash so only the canary directory itself is excluded, not any future
# sibling whose name shares the prefix.
${CHECKOV} -d "${AWS_ROOT}" --framework terraform \
    --check "${CHECKS}" \
    --skip-path "scripts/tests/checkov_canary/" \
    --skip-download --compact --quiet
