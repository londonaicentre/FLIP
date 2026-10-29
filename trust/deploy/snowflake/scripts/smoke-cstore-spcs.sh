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
# C-STORE smoke test for the Snowflake trust's XNAT DICOM receiver (FLIP#1583).
#
# The Snowflake twin of trust/deploy/helm/scripts/smoke-cstore.sh, and it exists for the
# same reason: a C-ECHO completes inside the DICOM association layer and never reaches
# XNAT's importer, so a trust can have a green echo, a READY service and an HTTP-200 site
# while every single store aborts (FLIP#1228). This drives a real store through the mocked
# PACS, then asks the RECEIVER what became of the object.
#
# WHAT IS DIFFERENT ON SNOWFLAKE
#
# The Helm script shells into the xnat-web pod and reads received.log. There is no exec on
# SPCS, so the evidence is gathered two other ways, both from inside this job service:
#
#   1. XNAT's own REST API: a new prearchive entry, or a new archived session.
#   2. The XNAT_ARCHIVE stage, mounted read-only here, gaining files. XNAT's directArchive
#      writes straight onto that stage (spike FLIP#1577), so its growth is receiver-side
#      evidence rather than association-layer evidence.
#
# (1) is the real check. (2) alone is reported as INCONCLUSIVE (exit 2), exactly as the
# Helm script treats a prearchive arrival-time match: a concurrent DQR import looks the
# same, and a stage mount is cached, so a count that moved is not proof this store moved it.
#
# HOW IT SENDS
#
# Through the mocked PACS, as on Kubernetes: Orthanc already has XNAT registered as a DICOM
# modality (the orthanc spec's XNAT_AETITLE / XNAT_HOST / XNAT_PORT), so
# POST /modalities/<key>/store opens the same association the real retrieval path opens
# after a C-MOVE. Real study bytes, no extra image, no synthetic object.
#
# Exit codes: 0 = the store completed and XNAT recorded the object.
#             1 = a real failure, with the reason named.
#             2 = inconclusive: the store completed and the only evidence was the archive
#                 stage's file count. `make smoke-cstore` maps 2 to a loud warning.

set -euo pipefail

ORTHANC_URL="${ORTHANC_URL:?ORTHANC_URL must be set}"
ORTHANC_USERNAME="${ORTHANC_USERNAME:-admin}"
ORTHANC_PASSWORD="${ORTHANC_PASSWORD:?ORTHANC_PASSWORD must be set}"
XNAT_URL="${XNAT_URL:?XNAT_URL must be set}"
XNAT_SERVICE_USER="${XNAT_SERVICE_USER:?XNAT_SERVICE_USER must be set}"
XNAT_SERVICE_PASSWORD="${XNAT_SERVICE_PASSWORD:?XNAT_SERVICE_PASSWORD must be set}"
# The key under which Orthanc knows XNAT. flip-entrypoint.sh registers the modality under
# the AE title, so this is XNAT_AETITLE unless an operator renamed it.
XNAT_MODALITY="${XNAT_MODALITY:-${XNAT_AETITLE:-XNAT}}"
# The XNAT_ARCHIVE stage, mounted read-only. Empty disables evidence (2).
ARCHIVE_DIR="${ARCHIVE_DIR:-/archive}"
# How long the receiver has to record the object before the run is called a failure.
EVIDENCE_TIMEOUT_SECONDS="${EVIDENCE_TIMEOUT_SECONDS:-180}"
POLL_SECONDS="${POLL_SECONDS:-10}"

# Credentials via 0600 netrc files, never argv (see seed-orthanc-spcs.sh for why).
orthanc_netrc="$(mktemp)"
xnat_netrc="$(mktemp)"
chmod 600 "$orthanc_netrc" "$xnat_netrc"
trap 'rm -f "$orthanc_netrc" "$xnat_netrc"' EXIT
_host() { local url="${1#*://}"; printf '%s' "${url%%[:/]*}"; }
printf 'machine %s login %s password %s\n' "$(_host "$ORTHANC_URL")" \
  "$ORTHANC_USERNAME" "$ORTHANC_PASSWORD" > "$orthanc_netrc"
printf 'machine %s login %s password %s\n' "$(_host "$XNAT_URL")" \
  "$XNAT_SERVICE_USER" "$XNAT_SERVICE_PASSWORD" > "$xnat_netrc"

orthanc() { curl --silent --show-error --netrc-file "$orthanc_netrc" "$@"; }
xnat() { curl --silent --show-error --netrc-file "$xnat_netrc" "$@"; }

fail() { echo "❌ $*" >&2; exit 1; }

# ── 0. Both ends must be answering, or the result says nothing about the DICOM path ──
orthanc --fail --output /dev/null "${ORTHANC_URL}/system" || fail "Orthanc is not answering at ${ORTHANC_URL}."
xnat --fail --output /dev/null "${XNAT_URL}/data/JSESSION" || fail "XNAT did not authenticate the service account at ${XNAT_URL}."

# ── 1. Pick an instance the PACS already holds ──────────────────────────────────────
instance=$(orthanc "${ORTHANC_URL}/instances?limit=1" | jq -r '.[0] // empty')
[ -n "$instance" ] || fail "Orthanc holds no instances. Seed it first: make seed-orthanc KIT=<CODE>."
sop=$(orthanc "${ORTHANC_URL}/instances/${instance}" | jq -r '.MainDicomTags.SOPInstanceUID // empty')
echo "Sending instance ${instance} (SOPInstanceUID ${sop:-unknown}) to modality ${XNAT_MODALITY}."

# ── 2. Mark the receiver's state BEFORE the store ───────────────────────────────────
# Counted, not listed: XNAT's prearchive listing is per project and a direct-archived
# object never appears there at all, so a count over both views is the only reading that
# covers the two import paths a FLIP receiver uses.
_prearchive_count() { xnat "${XNAT_URL}/data/prearchive/projects?format=json" | jq '[.ResultSet.Result[]?] | length' 2>/dev/null || echo 0; }
_session_count() { xnat "${XNAT_URL}/data/experiments?format=json" | jq '[.ResultSet.Result[]?] | length' 2>/dev/null || echo 0; }
_archive_files() { [ -d "$ARCHIVE_DIR" ] && find "$ARCHIVE_DIR" -type f 2>/dev/null | wc -l || echo 0; }

before_prearchive=$(_prearchive_count)
before_sessions=$(_session_count)
before_archive=$(_archive_files)
echo "Before: prearchive=${before_prearchive} sessions=${before_sessions} archive files=${before_archive}"

# ── 3. The store itself ─────────────────────────────────────────────────────────────
store=$(orthanc --request POST --header 'Content-Type: application/json' \
  --data "$(jq -nc --arg id "$instance" '{Resources: [$id], Synchronous: true}')" \
  "${ORTHANC_URL}/modalities/${XNAT_MODALITY}/store") || fail "POST /modalities/${XNAT_MODALITY}/store failed."
failed_count=$(printf '%s' "$store" | jq -r '.FailedInstancesCount // 0')
echo "Orthanc: ${store}"
[ "$failed_count" = "0" ] || fail "Orthanc reported ${failed_count} failed instance(s): the association aborted."

# ── 4. What the receiver did with it ────────────────────────────────────────────────
verdict="none"
deadline=$(( $(date +%s) + EVIDENCE_TIMEOUT_SECONDS ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  sleep "$POLL_SECONDS"
  if [ "$(_session_count)" -gt "$before_sessions" ]; then verdict="session"; break; fi
  if [ "$(_prearchive_count)" -gt "$before_prearchive" ]; then verdict="prearchive"; break; fi
  if [ "$(_archive_files)" -gt "$before_archive" ]; then verdict="archive-stage"; fi
done

case "$verdict" in
  session)
    echo "✅ C-STORE smoke PASSED: XNAT archived a new session for the object it received."
    exit 0
    ;;
  prearchive)
    echo "✅ C-STORE smoke PASSED: the object reached XNAT's prearchive."
    exit 0
    ;;
  archive-stage)
    echo "⚠️  C-STORE smoke INCONCLUSIVE: the store completed and the XNAT_ARCHIVE stage" >&2
    echo "⚠️  gained files, but XNAT recorded neither a prearchive entry nor a session." >&2
    echo "⚠️  A concurrent DQR import looks the same and a stage mount is cached, so this" >&2
    echo "⚠️  is NOT a verified DICOM path. Re-run with the PACS idle." >&2
    exit 2
    ;;
  *)
    fail "the store completed but XNAT showed no sign of the object within ${EVIDENCE_TIMEOUT_SECONDS}s.
   This is the FLIP#1228 signature: the association succeeded and the importer rejected the
   object. Read the receiver's log:
     SELECT SYSTEM\$GET_SERVICE_LOGS('<db>.APP.XNAT_WEB', 0, 'xnat-web', 200);
   and check that the xnat-configure job has run (until it does, every C-STORE is rejected)."
    ;;
esac
