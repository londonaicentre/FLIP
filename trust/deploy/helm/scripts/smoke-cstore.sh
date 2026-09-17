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
# AbstractMethodError on the FIRST object the importer hands to it — so a trust can
# have a green C-ECHO, a Ready xnat-web pod, a deployed Helm release and an HTTP-200
# site, and still abort every single transfer. That is FLIP#1228 exactly. This script
# therefore drives a real store and then reads the receiver's own log, because the
# association status alone reproduces the false confidence the bug was made of.
#
# HOW IT SENDS
#
# Through the mocked PACS, not a synthetic sender: the chart already registers XNAT
# as a DICOM modality in Orthanc (templates/orthanc.yaml, ORTHANC__DICOM_MODALITIES),
# so `POST /modalities/<AE>/store` makes Orthanc open the same association the real
# retrieval path opens after a C-MOVE. No extra image, no extra pod, no NetworkPolicy
# exception, and the bytes are real study data rather than a minimal fabricated object.
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
# The modality key in ORTHANC__DICOM_MODALITIES, not the AE title (they happen to
# match in the shipped config).
ORTHANC_MODALITY="${ORTHANC_MODALITY:-XNAT}"
# Override to store a known instance rather than whichever one Orthanc lists first.
INSTANCE_ID="${INSTANCE_ID:-}"
DICOM_LOG="${DICOM_LOG:-/data/xnat/home/logs/dicom.log}"

# Importer failures, as they appear in dicom.log. The first is the version-mismatch
# crash itself; the second is how the same association looks from the receiving end
# once the importer has already thrown.
FAILURE_PATTERNS='AbstractMethodError|NoSuchMethodError|unable to read DICOM object null'

red() { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
info() { printf '\033[0;34m%s\033[0m\n' "$*"; }

fail() {
  red "✗ $*"
  exit 1
}

pod_for() {
  # $1 = component label. Prefer the release's own pod, fall back to component-only
  # for a pod that predates the instance label.
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

# Run a curl against Orthanc's REST API from INSIDE the orthanc pod, so the
# registered-user credential is read out of the pod's own environment and never
# reaches this machine, this terminal, or a process list.
orthanc_curl() {
  kubectl exec -n "$NAMESPACE" "$ORTHANC_POD" -- sh -c '
    creds=$(printf "%s" "$ORTHANC__REGISTERED_USERS" \
      | sed -n "s/.*\"\([^\"]*\)\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1:\2/p")
    [ -n "$creds" ] || { echo "could not read ORTHANC__REGISTERED_USERS" >&2; exit 1; }
    exec curl -sS -u "$creds" "$@"
  ' -- "$@"
}

info "▶  C-STORE smoke — namespace ${NAMESPACE}, release ${RELEASE_NAME}"

ORTHANC_POD=$(pod_for orthanc)
[ -n "$ORTHANC_POD" ] || fail "no running orthanc pod in ${NAMESPACE} — this smoke stores through the mocked PACS"
XNAT_POD=$(pod_for xnat-web)
[ -n "$XNAT_POD" ] || fail "no running xnat-web pod in ${NAMESPACE}"
info "   sender: ${ORTHANC_POD}   receiver: ${XNAT_POD}"

# ── 1. Mark the receiver log, so only lines this transfer produces are judged ────────
# A trust that has been running for weeks has old errors in dicom.log; scanning the
# whole file would fail on history and hide today's result.
LOG_MARK=$(kubectl exec -n "$NAMESPACE" "$XNAT_POD" -- \
  sh -c "wc -l < '${DICOM_LOG}' 2>/dev/null || echo 0" | tr -d '[:space:]')
LOG_MARK="${LOG_MARK:-0}"
info "   ${DICOM_LOG} is ${LOG_MARK} lines before the store"

# ── 2. Pick something to send ────────────────────────────────────────────────────────
if [ -z "$INSTANCE_ID" ]; then
  INSTANCE_ID=$(orthanc_curl 'http://localhost:8042/instances?limit=1' \
    | tr -d '[]" \n' | cut -d, -f1)
fi
[ -n "$INSTANCE_ID" ] || fail "Orthanc holds no instances — seed the PACS first, or pass INSTANCE_ID=<orthanc instance id>"
info "   storing instance ${INSTANCE_ID} → modality ${ORTHANC_MODALITY}"

# ── 3. The store itself ──────────────────────────────────────────────────────────────
# Synchronous on purpose: an asynchronous job would let this script exit before the
# receiver has done anything, which is the failure mode it is meant to detect.
STORE_OUTPUT=$(orthanc_curl -X POST \
  "http://localhost:8042/modalities/${ORTHANC_MODALITY}/store" \
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
sleep 5
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
