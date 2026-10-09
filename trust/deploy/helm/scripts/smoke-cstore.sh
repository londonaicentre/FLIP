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
# drives a real store and then checks what the RECEIVER did with the object, because the
# association status alone reproduces the false confidence the bug was made of.
#
# WHAT COUNTS AS SUCCESS
#
# Orthanc reporting 0 failed instances AND receiver-side evidence that the object
# landed, time-boxed poll, strongest first:
#
#   1. a new prearchive object carrying the SOPInstanceUID (else StudyInstanceUID)
#      that was sent;
#   2. a new line in XNAT's `received.log` since the mark taken before the store;
#   3. a new prearchive object by arrival time alone — a genuine last resort, and
#      reported as such.
#
# (2) is the normal path on a FLIP-configured receiver: configure-xnat.sh sets
# `anonymizationEnabled: true` and anon_script.das rewrites the Study, Series and SOP
# UIDs through hashUID, so the object XNAT stores does NOT carry the UIDs Orthanc holds
# and (1) cannot match. Without (2) every healthy run would fall through to (3) and pass
# on "something arrived", which a concurrent DQR/C-MOVE import also satisfies.
#
# `received.log` is where XNAT records a successful receipt; `dicom.log` is its ERROR
# log. The original check required `dicom.log` to gain lines — the wrong logger, and one
# this deployment never writes at all (0 bytes while every store imports correctly), so
# a healthy ingest path reported failure. Its growth is now reported as context and is
# never a success route. The FLIP#1228 regression signatures still fail the run
# outright, read from both dicom.log and the xnat-web container log. The decision itself
# lives in scripts/cstore_verdict.py (unit-tested; this script only gathers evidence,
# and hands it over through the environment so no untested logic lives here).
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
#   KUBE_CONTEXT=kind-flip make -C trust/deploy/helm smoke-cstore   # pick the cluster
#
# Exit codes: 0 = the store completed and the receiver logged no importer failure.
#             1 = anything else (with the reason named).

set -euo pipefail

NAMESPACE="${NAMESPACE:-flip-trust}"
RELEASE_NAME="${RELEASE_NAME:-trust-release}"
# Which cluster to act on, as everywhere else in this chart's tooling. A box running
# several kind clusters otherwise gets whichever one kubectl currently points at — and
# this smoke both reads a Secret and drives a transfer, so the wrong one is worth more
# than a confusing result. Every kubectl below goes through $KUBECTL.
# (An `if` rather than `[ -n … ] && KUBECTL=…`: under `set -e` that AND-list exits the
# script whenever the test fails, which is the default case of an unset context.)
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
KUBECTL=(kubectl)
if [ -n "$KUBE_CONTEXT" ]; then
  KUBECTL=(kubectl --context "$KUBE_CONTEXT")
fi
# The modality KEY in ORTHANC__DICOM_MODALITIES, which is not necessarily the AE title
# (they happen to match in the shipped config).
ORTHANC_MODALITY="${ORTHANC_MODALITY:-XNAT}"
ORTHANC_URL="${ORTHANC_URL:-http://orthanc:8042}"
# Override to store a known instance rather than whichever one Orthanc lists first.
INSTANCE_ID="${INSTANCE_ID:-}"
DICOM_LOG="${DICOM_LOG:-/data/xnat/home/logs/dicom.log}"
# Where XNAT records a SUCCESSFUL receipt: one line per object, naming the calling AE and
# the file it wrote, both AFTER the receiver's anonymisation script has run. That is what
# makes it the evidence that survives hashUID; dicom.log above is the error log.
RECEIVED_LOG="${RECEIVED_LOG:-/data/xnat/home/logs/received.log}"
# Orthanc's own calling AE, as it appears at the head of a received.log line ("<AE>@/ip:port:").
# Used only to prefer this sender's lines over a concurrent import's; an empty or wrong value
# degrades to "any new line", never to a failure.
SENDER_AE="${SENDER_AE:-ORTHANC}"
# Where XNAT parks received objects before they are archived. The positive evidence this
# smoke now requires lives here.
PREARCHIVE_DIR="${PREARCHIVE_DIR:-/data/xnat/prearchive}"
# How long to let the receiver write its side of the story before reading the log.
SETTLE_SECONDS="${SETTLE_SECONDS:-5}"
# Time box for the prearchive poll: the importer runs asynchronously after the
# association closes, so the object can land seconds after Orthanc reports success.
PREARCHIVE_TIMEOUT="${PREARCHIVE_TIMEOUT:-90}"
PREARCHIVE_INTERVAL="${PREARCHIVE_INTERVAL:-3}"
PYTHON="${PYTHON:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Importer failures — AbstractMethodError / NoSuchMethodError / "unable to read DICOM
# object null" — are matched by scripts/cstore_verdict.py, which owns the verdict. The
# first two are the version-mismatch crash itself; the third is how the same association
# looks from the receiving end once the importer has already thrown. They are listed once,
# there, so the shell and the unit tests cannot drift apart.

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
  pod=$("${KUBECTL[@]}" get pods -n "$NAMESPACE" \
    -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=${component}" \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [ -z "$pod" ]; then
    pod=$("${KUBECTL[@]}" get pods -n "$NAMESPACE" \
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
  "${KUBECTL[@]}" get deploy -n "$NAMESPACE" \
    -l "app.kubernetes.io/component=orthanc" \
    -o jsonpath='{.items[0].spec.template.spec.containers[0].env[?(@.name=="ORTHANC__REGISTERED_USERS")].valueFrom.secretKeyRef.name}' \
    2>/dev/null || true
}

# Run one curl against Orthanc's REST API from inside the XNAT pod. The credential
# arrives on stdin and is read into a shell variable there: never an argument, so it
# reaches no process list on the node and no shell history here.
orthanc_curl() {
  printf '%s' "$ORTHANC_CREDS" | "${KUBECTL[@]}" exec -i -n "$NAMESPACE" "$XNAT_POD" -- sh -c '
    read -r creds || true
    [ -n "$creds" ] || { echo "no Orthanc credential on stdin" >&2; exit 1; }
    exec curl -fsS --max-time 300 -u "$creds" "$@"
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
# Each substitution below names its own failure. Under `set -euo pipefail` a failing
# kubectl inside `$(…)` otherwise aborts the script on kubectl's stderr alone, with none
# of the "reason named" this header promises.
ORTHANC_CREDS=$("${KUBECTL[@]}" get secret "$SECRET_NAME" -n "$NAMESPACE" \
  -o jsonpath='{.data.orthanc-registered-users}' | base64 -d \
  | sed -n 's/.*"\([^"]*\)"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1:\2/p') \
  || fail "could not read ${SECRET_NAME}/orthanc-registered-users — is the Secret present and readable?"
[ -n "$ORTHANC_CREDS" ] || fail "could not read a user out of ${SECRET_NAME}/orthanc-registered-users"

# ── 1. Mark the receiver, so only what this transfer produced is judged ──────────────
# A trust that has been running for weeks has old errors in dicom.log, old receipts in
# received.log and old studies in the prearchive; scanning any of them whole would fail
# on history and hide today's result. One exec reads both marks.
MARKS=$("${KUBECTL[@]}" exec -n "$NAMESPACE" "$XNAT_POD" -- \
  env D="$DICOM_LOG" R="$RECEIVED_LOG" sh -c \
  'printf "%s %s %s\n" "$(wc -l < "$D" 2>/dev/null || echo 0)" "$(wc -l < "$R" 2>/dev/null || echo 0)" "$(wc -c < "$R" 2>/dev/null || echo 0)"') \
  || fail "could not read ${DICOM_LOG} / ${RECEIVED_LOG} in ${XNAT_POD} — without a mark this smoke cannot tell this transfer's log lines from the pod's history"
LOG_MARK=$(printf '%s' "$MARKS" | awk '{print $1+0}')
RECEIVED_MARK=$(printf '%s' "$MARKS" | awk '{print $2+0}')
RECEIVED_BYTES=$(printf '%s' "$MARKS" | awk '{print $3+0}')
LOG_MARK="${LOG_MARK:-0}"
RECEIVED_MARK="${RECEIVED_MARK:-0}"
RECEIVED_BYTES="${RECEIVED_BYTES:-0}"
info "   ${DICOM_LOG} is ${LOG_MARK} lines, ${RECEIVED_LOG} is ${RECEIVED_MARK} lines before the store"

# The receiver's OWN clock, as epoch seconds AND as the RFC3339 instant that epoch denotes.
# Epoch is absolute, so `find -newermt @N` below compares like with like however the
# container's TZ is set (the trust's XNAT runs UTC while the operator may not) — never
# format a local timestamp and hand it across. The RFC3339 form is for `kubectl logs
# --since-time`: an elapsed `--since=Ns` window is measured from when the log is READ, which
# on a run that polls to the deadline is long after the store, and would drop exactly the
# first seconds in which an importer AbstractMethodError is thrown.
CLOCK=$("${KUBECTL[@]}" exec -n "$NAMESPACE" "$XNAT_POD" -- sh -c \
  'S=$(date -u +%s); S=$((S-1)); printf "%s %s\n" "$S" "$(date -u -d "@$S" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"') \
  || fail "could not read the clock in ${XNAT_POD} — without it this smoke cannot tell a new prearchive object from an old one"
# One second of slack is already subtracted above: `date` truncates and a file written in
# the same second can carry a marginally earlier mtime than the string we just read.
RUN_START=$(printf '%s' "$CLOCK" | awk '{print $1}' | tr -d '[:space:]')
RUN_START_RFC3339=$(printf '%s' "$CLOCK" | awk '{print $2}' | tr -d '[:space:]')
case "$RUN_START" in
  ''|*[!0-9]*) fail "the clock in ${XNAT_POD} did not return epoch seconds (got '${RUN_START}')" ;;
esac

# ── 2. Pick something to send ────────────────────────────────────────────────────────
if [ -z "$INSTANCE_ID" ]; then
  # `since` and `limit` go together: Orthanc rejects either one alone with a 400. With
  # `curl -f` that is now a non-zero exit named here, rather than an error body mangled
  # into an "id" — and the same guard distinguishes a bad credential (401, empty body)
  # from a genuinely empty PACS, which otherwise both read as "holds no instances".
  INSTANCE_ID=$(orthanc_curl "${ORTHANC_URL}/instances?since=0&limit=1" | tr -d '[]" \n' | cut -d, -f1) \
    || fail "could not list Orthanc's instances at ${ORTHANC_URL} — check the credential in ${SECRET_NAME} and that Orthanc is reachable from ${XNAT_POD}"
fi
[ -n "$INSTANCE_ID" ] || fail "Orthanc holds no instances — seed the PACS first, or pass INSTANCE_ID=<orthanc instance id>"
# An Orthanc resource id is 8 dash-separated hex groups. Anything else is an error body
# that survived the tr/cut above; sending it would fail at the store with Orthanc's words
# rather than these, hiding whether the PACS or the receiver is at fault.
case "$INSTANCE_ID" in
  *[!0-9a-f-]*)
    red "$INSTANCE_ID"
    fail "Orthanc did not return an instance id — the listing above is what it said instead"
    ;;
esac
info "   storing instance ${INSTANCE_ID} → modality ${ORTHANC_MODALITY}"

# The UIDs that identify the object on the receiving side, where the receiver stores what
# it was given. On a FLIP-configured receiver it does not — anonymizationEnabled +
# anon_script.das hash the Study/Series/SOP UIDs — so this match is a bonus for receivers
# without that script, and received.log carries the evidence here. A lookup failure is not
# fatal for the same reason.
instance_tag() {
  orthanc_curl "${ORTHANC_URL}/instances/${INSTANCE_ID}$1" 2>/dev/null \
    | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -1 || true
}
SOP_UID=$(instance_tag "" SOPInstanceUID)
STUDY_UID=$(instance_tag "/study" StudyInstanceUID)
case "$SOP_UID" in *[!0-9.]*) SOP_UID="" ;; esac
case "$STUDY_UID" in *[!0-9.]*) STUDY_UID="" ;; esac
if [ -n "$SOP_UID" ] || [ -n "$STUDY_UID" ]; then
  info "   sent SOPInstanceUID=${SOP_UID:-?} StudyInstanceUID=${STUDY_UID:-?}"
else
  info "   could not read the instance's UIDs from Orthanc — the prearchive check will match on arrival time only"
fi

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

if [ -z "$FAILED_COUNT" ] || [ "$FAILED_COUNT" != "0" ]; then
  red "$STORE_OUTPUT"
fi

# ── 4. What the RECEIVER did with the object ─────────────────────────────────────────
# This is the assertion that separates this smoke from a connectivity check. XNAT answers
# the association at the network layer and only then hands the object to the importer, so
# a plugin/core mismatch shows up here and nowhere else.
#
# The evidence is received.log and the prearchive, not dicom.log: dicom.log is the error
# log (and stays 0 bytes on this deployment), so its growth is never success. The import
# is asynchronous, so poll.
sleep "$SETTLE_SECONDS"

# One remote command per poll: list the DICOM objects written since RUN_START, and for
# each, whether it carries the sent UID (in its path — XNAT names prearchive files after
# the SOPInstanceUID — or in its bytes). Output is "match-mode<TAB>path" per line.
prearchive_scan() {
  "${KUBECTL[@]}" exec -n "$NAMESPACE" "$XNAT_POD" -- \
    env PA="$PREARCHIVE_DIR" START="$RUN_START" SOP="$SOP_UID" STUDY="$STUDY_UID" sh -c '
      [ -d "$PA" ] || exit 0
      find "$PA" -type f \( -name "*.dcm" -o -name "*.DCM" -o -name "*.dicom" \) \
        -newermt "@$START" 2>/dev/null | while IFS= read -r f; do
        mode=time
        if [ -n "$SOP" ]; then
          case "$f" in *"$SOP"*) mode=sop ;; esac
          if [ "$mode" = time ] && grep -qa -- "$SOP" "$f" 2>/dev/null; then mode=sop; fi
        fi
        if [ "$mode" = time ] && [ -n "$STUDY" ]; then
          case "$f" in *"$STUDY"*) mode=study ;; esac
          if [ "$mode" = time ] && grep -qa -- "$STUDY" "$f" 2>/dev/null; then mode=study; fi
        fi
        printf "%s\t%s\n" "$mode" "$f"
      done
    ' 2>/dev/null || true
}

# Everything received.log gained since the mark. This survives the receiver's anonymisation
# script, which is why it and not the UID match is the primary evidence here.
# If the file rotated between the mark and the read, the line-count mark points past the end of
# the new file and `tail -n +N` would read nothing — a false fail. Detect it by the byte size
# recorded with the mark: a file smaller than it was has been rotated or truncated, so read it
# from line 1 instead.
received_since_mark() {
  "${KUBECTL[@]}" exec -n "$NAMESPACE" "$XNAT_POD" -- \
    env R="${RECEIVED_LOG}" M="$RECEIVED_MARK" B="$RECEIVED_BYTES" sh -c '
      now=$(wc -c < "$R" 2>/dev/null || echo 0)
      from=$((M + 1))
      [ "$now" -lt "$B" ] && from=1
      tail -n +"$from" "$R" 2>/dev/null || true
    ' 2>/dev/null || true
}

SCAN=""
NEW_RECEIVED=""
TIMED_OUT=1
DEADLINE=$((SECONDS + PREARCHIVE_TIMEOUT))
while :; do
  SCAN=$(prearchive_scan)
  NEW_RECEIVED=$(received_since_mark)
  # Stop as soon as there is conclusive evidence — a UID-matched object or a logged receipt.
  # Otherwise keep polling until the box expires, so the arrival-time-only reading is
  # reported only when nothing better ever arrives.
  if printf '%s\n' "$SCAN" | grep -Eq '^(sop|study)[[:space:]]' || [ -n "${NEW_RECEIVED//[[:space:]]/}" ]; then
    TIMED_OUT=0
    break
  fi
  [ "$SECONDS" -lt "$DEADLINE" ] || break
  sleep "$PREARCHIVE_INTERVAL"
done

NEW_LOG=$("${KUBECTL[@]}" exec -n "$NAMESPACE" "$XNAT_POD" -- \
  sh -c "tail -n +$((LOG_MARK + 1)) '${DICOM_LOG}' 2>/dev/null || true")
# The container log too: on a deployment that never writes dicom.log, an importer
# AbstractMethodError still surfaces on stdout, and that regression must keep failing.
# The window starts at RUN_START, not "the last PREARCHIVE_TIMEOUT seconds": this line runs
# after SETTLE_SECONDS plus a possibly-full poll, so an elapsed window would begin well
# after the store and miss the crash entirely. `--since` is only the fallback for a
# receiver whose `date` could not produce an RFC3339 instant.
if [ -n "$RUN_START_RFC3339" ]; then
  POD_LOG=$("${KUBECTL[@]}" logs -n "$NAMESPACE" "$XNAT_POD" --since-time="$RUN_START_RFC3339" --tail=2000 2>/dev/null || true)
else
  POD_LOG=$("${KUBECTL[@]}" logs -n "$NAMESPACE" "$XNAT_POD" --since="$((SETTLE_SECONDS + PREARCHIVE_TIMEOUT + 30))s" --tail=2000 2>/dev/null || true)
fi

command -v "$PYTHON" >/dev/null 2>&1 \
  || fail "${PYTHON} not found — the smoke's verdict logic (scripts/cstore_verdict.py) needs a Python 3 interpreter; set PYTHON=<path>"

# Hand the evidence to the unit-tested decision function, which parses it, prints the verdict
# and owns the exit status. Everything above only gathers; nothing above judges, and no
# parsing happens here — `cstore_verdict.build_evidence` reads these variables itself, so the
# precedence rules it applies are the ones its tests cover.
set +e
NEW_LOG="$NEW_LOG" POD_LOG="$POD_LOG" SCAN="$SCAN" NEW_RECEIVED="$NEW_RECEIVED" \
  FAILED_COUNT="$FAILED_COUNT" INSTANCE_COUNT="$INSTANCE_COUNT" \
  SOP_UID="$SOP_UID" STUDY_UID="$STUDY_UID" SENDER_AE="$SENDER_AE" \
  TIMED_OUT="$TIMED_OUT" PREARCHIVE_TIMEOUT="$PREARCHIVE_TIMEOUT" \
  "$PYTHON" "${SCRIPT_DIR}/cstore_verdict.py" --from-env
STATUS=$?
set -e
exit "$STATUS"

