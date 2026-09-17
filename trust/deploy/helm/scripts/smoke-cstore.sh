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
# C-STORE smoke test for the K8s trust's XNAT DICOM receiver (FLIP#1228).
#
# WHY THIS EXISTS, AND WHY C-ECHO IS NOT ENOUGH
#
# C-ECHO never reaches XNAT's importer: it completes inside the DICOM association
# layer. A plugin compiled against a different XNAT core than the one running throws
# AbstractMethodError on the FIRST object the importer handles — so a trust can have a
# green C-ECHO, a Ready xnat-web pod, a deployed Helm release and an HTTP-200 site, and
# still abort every single transfer. That is FLIP#1228 exactly. This script therefore
# drives a real store and then reads the receiver's own log, because the association
# status alone reproduces the false confidence the bug was made of.
#
# HOW IT SENDS
#
# Through the mocked PACS, not a synthetic sender: the chart already registers XNAT as
# a DICOM modality in Orthanc (templates/orthanc.yaml, ORTHANC__DICOM_MODALITIES), so
# `POST /modalities/<key>/store` makes Orthanc open the same association the real
# retrieval path opens after a C-MOVE. No extra image, no extra pod, no NetworkPolicy
# exception, and the bytes are real study data rather than a fabricated object.
#
# The REST calls are issued from the XNAT pod rather than from Orthanc's own: curl is
# guaranteed there (configure-xnat.sh, which runs from that image, is built on it) while
# the upstream Orthanc image promises no HTTP client at all. Both pods sit in the
# namespace the egress policy allows intra-namespace traffic within.
#
# Usage:
#   make -C trust/deploy/helm smoke-cstore
#   NAMESPACE=flip-trust RELEASE_NAME=trust-release ./scripts/smoke-cstore.sh
#
# Exit codes: 0 = the store completed and the receiver logged no importer failure.
#             1 = anything else (with the reason named).

set -euo pipefail

NAMESPACE="${NAMESPACE:-flip-trust}"
RELEASE_NAME="${RELEASE_NAME:-trust-release}"
# The modality KEY in ORTHANC__DICOM_MODALITIES, which is not necessarily the AE title
# (they happen to match in the shipped config).
ORTHANC_MODALITY="${ORTHANC_MODALITY:-XNAT}"
ORTHANC_URL="${ORTHANC_URL:-http://orthanc:8042}"
# Override to store a known instance rather than whichever one Orthanc lists first.
INSTANCE_ID="${INSTANCE_ID:-}"
DICOM_LOG="${DICOM_LOG:-/data/xnat/home/logs/dicom.log}"
# How long to let the receiver write its side of the story before reading the log.
SETTLE_SECONDS="${SETTLE_SECONDS:-5}"

# Importer failures, as they appear in dicom.log. The first two are the version-mismatch
# crash itself; the third is how the same association looks from the receiving end once
# the importer has already thrown.
FAILURE_PATTERNS='AbstractMethodError|NoSuchMethodError|unable to read DICOM object null'

red() { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
info() { printf '\033[0;34m%s\033[0m\n' "$*"; }

fail() {
  red "✗ $*"
  exit 1
}

pod_for() {
  # $1 = component label. Prefer the release's own pod, fall back to component-only for
  # a pod that predates the instance label.
  local component="$1" pod
  pod=$(kubectl get pods -n "$NAMESPACE" \
    -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=${component}" \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [ -z "$pod" ]; then
    pod=$(kubectl get pods -n "$NAMESPACE" \
      -l "app.kubernetes.io/component=${component}" \
      --field-selector=status.phase=Running \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  fi
  printf '%s' "$pod"
}

# Resolve the Secret holding Orthanc's registered users from the Deployment that
# consumes it, rather than reconstructing the chart's naming here: `secrets.create`
# false points every service at an operator-supplied `secrets.existingName`, and a
# guessed name would fail as "no credential" on a perfectly healthy trust.
orthanc_secret_ref() {
  kubectl get deploy -n "$NAMESPACE" \
    -l "app.kubernetes.io/component=orthanc" \
    -o jsonpath='{.items[0].spec.template.spec.containers[0].env[?(@.name=="ORTHANC__REGISTERED_USERS")].valueFrom.secretKeyRef.name}' \
    2>/dev/null || true
}

# Run one curl against Orthanc's REST API from inside the XNAT pod. The credential
# arrives on stdin and is read into a shell variable there: never an argument, so it
# reaches no process list on the node and no shell history here.
orthanc_curl() {
  printf '%s' "$ORTHANC_CREDS" | kubectl exec -i -n "$NAMESPACE" "$XNAT_POD" -- sh -c '
    read -r creds || true
    [ -n "$creds" ] || { echo "no Orthanc credential on stdin" >&2; exit 1; }
    exec curl -sS --max-time 300 -u "$creds" "$@"
  ' -- "$@"
}

info "▶  C-STORE smoke — namespace ${NAMESPACE}, release ${RELEASE_NAME}"

ORTHANC_POD=$(pod_for orthanc)
[ -n "$ORTHANC_POD" ] || fail "no running orthanc pod in ${NAMESPACE} — this smoke stores through the mocked PACS"
XNAT_POD=$(pod_for xnat-web)
[ -n "$XNAT_POD" ] || fail "no running xnat-web pod in ${NAMESPACE}"
info "   sender: ${ORTHANC_POD}   receiver: ${XNAT_POD}"

SECRET_NAME=$(orthanc_secret_ref)
[ -n "$SECRET_NAME" ] || fail "could not find the Secret behind ORTHANC__REGISTERED_USERS on the orthanc Deployment"
# {"user":"pass"} → user:pass. Orthanc's REST API needs a registered user; the DICOM
# association it then opens does not, which is why this credential is only about
# *asking* for the store and says nothing about the transfer's own authentication.
ORTHANC_CREDS=$(kubectl get secret "$SECRET_NAME" -n "$NAMESPACE" \
  -o jsonpath='{.data.orthanc-registered-users}' | base64 -d \
  | sed -n 's/.*"\([^"]*\)"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1:\2/p')
[ -n "$ORTHANC_CREDS" ] || fail "could not read a user out of ${SECRET_NAME}/orthanc-registered-users"

# ── 1. Mark the receiver log, so only lines this transfer produces are judged ────────
# A trust that has been running for weeks has old errors in dicom.log; scanning the
# whole file would fail on history and hide today's result.
LOG_MARK=$(kubectl exec -n "$NAMESPACE" "$XNAT_POD" -- \
  sh -c "wc -l < '${DICOM_LOG}' 2>/dev/null || echo 0" | tr -d '[:space:]')
LOG_MARK="${LOG_MARK:-0}"
info "   ${DICOM_LOG} is ${LOG_MARK} lines before the store"

# ── 2. Pick something to send ────────────────────────────────────────────────────────
if [ -z "$INSTANCE_ID" ]; then
  INSTANCE_ID=$(orthanc_curl "${ORTHANC_URL}/instances?limit=1" | tr -d '[]" \n' | cut -d, -f1)
fi
[ -n "$INSTANCE_ID" ] || fail "Orthanc holds no instances — seed the PACS first, or pass INSTANCE_ID=<orthanc instance id>"
info "   storing instance ${INSTANCE_ID} → modality ${ORTHANC_MODALITY}"

# ── 3. The store itself ──────────────────────────────────────────────────────────────
# Synchronous on purpose: an asynchronous job would let this script exit before the
# receiver has done anything, which is the failure mode it is meant to detect.
STORE_OUTPUT=$(orthanc_curl -X POST \
  "${ORTHANC_URL}/modalities/${ORTHANC_MODALITY}/store" \
  -H 'Content-Type: application/json' \
  -d "{\"Resources\":[\"${INSTANCE_ID}\"],\"Synchronous\":true}" 2>&1) || {
  red "$STORE_OUTPUT"
  fail "the C-STORE association failed outright — Orthanc could not store to ${ORTHANC_MODALITY}"
}

FAILED_COUNT=$(printf '%s' "$STORE_OUTPUT" \
  | sed -n 's/.*"FailedInstancesCount"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p')
INSTANCE_COUNT=$(printf '%s' "$STORE_OUTPUT" \
  | sed -n 's/.*"InstancesCount"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p')

if [ -z "$FAILED_COUNT" ]; then
  red "$STORE_OUTPUT"
  fail "Orthanc's store report had no FailedInstancesCount — cannot tell whether the transfer succeeded"
fi
if [ "$FAILED_COUNT" != "0" ]; then
  red "$STORE_OUTPUT"
  fail "Orthanc reports ${FAILED_COUNT} failed instance(s) — the receiver rejected or aborted the transfer"
fi
green "✓ store reported success (${INSTANCE_COUNT:-?} instance(s), 0 failed)"

# ── 4. Read the receiver's own log ───────────────────────────────────────────────────
# This is the assertion that separates this smoke from a connectivity check. XNAT
# answers the association at the network layer and only then hands the object to the
# importer, so a plugin/core mismatch shows up HERE and nowhere else.
sleep "$SETTLE_SECONDS"
NEW_LOG=$(kubectl exec -n "$NAMESPACE" "$XNAT_POD" -- \
  sh -c "tail -n +$((LOG_MARK + 1)) '${DICOM_LOG}' 2>/dev/null || true")

if printf '%s' "$NEW_LOG" | grep -Eq "$FAILURE_PATTERNS"; then
  red "--- ${DICOM_LOG} (lines written by this transfer) ---"
  printf '%s\n' "$NEW_LOG" | grep -E -A3 "$FAILURE_PATTERNS" || true
  red "---"
  fail "the receiver logged an importer failure. An AbstractMethodError here means an XNAT plugin was built against a different core than the one running — compare the pod's /data/xnat/home/plugins against xnat.web.plugins.urls (make status), then redeploy with a HELM_TIMEOUT above the init job's real duration. See TROUBLESHOOTING.md §2.7."
fi

if [ -z "$NEW_LOG" ]; then
  red "⚠  ${DICOM_LOG} gained no lines — the object may never have reached the importer"
  fail "no receiver-side evidence of the transfer; check that the SCP receiver is the one Orthanc dialled (make status, TROUBLESHOOTING.md §2.1)"
fi

green "✓ receiver logged the transfer with no importer failure"
green "✅ C-STORE smoke passed"
