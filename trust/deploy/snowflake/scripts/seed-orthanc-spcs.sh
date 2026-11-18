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
# Seed the trust's mocked PACS from the SEED stage, as an SPCS job service (FLIP#1583).
#
# The Snowflake twin of `make -C trust seed-orthanc`, which streams the published DICOM
# archive from Hugging Face and POSTs it to Orthanc. A trust on Snowflake has no such
# egress and no host to download onto, so the bytes arrive the other way: an operator
# puts them on @STAGES.SEED once (`snow stage copy`), this job mounts that stage and
# uploads what it finds. The selection the compose path makes from the OMOP tables is
# therefore made at PUT time, outside the account, not here.
#
# Stage layout, which this script defines and the README documents:
#
#   @FLIP_TRUST_<CODE>.STAGES.SEED/dicom/<project>/<accession>/<instance>.dcm
#
# Idempotent: Orthanc dedupes on SOPInstanceUID, so a re-run uploads the same instances
# and stores none of them again. That is what makes this safe to run from `make up`.
#
# Usage (normally through `make -C trust/deploy/snowflake seed-orthanc KIT=<CODE>`):
#   SEED_DIR=/seed ORTHANC_URL=http://orthanc...:8042 bash seed-orthanc-spcs.sh
#
# Exit codes: 0 = every instance found was accepted (or already held).
#             1 = Orthanc never came up, the stage held no DICOM, or an upload failed.

set -euo pipefail

SEED_DIR="${SEED_DIR:-/seed}"
ORTHANC_URL="${ORTHANC_URL:?ORTHANC_URL must be set}"
ORTHANC_USERNAME="${ORTHANC_USERNAME:-admin}"
ORTHANC_PASSWORD="${ORTHANC_PASSWORD:?ORTHANC_PASSWORD must be set}"
# Space or comma separated. Empty means every project directory on the stage.
PROJECTS="${PROJECTS:-}"
# Seconds to wait for Orthanc to answer /system before giving up.
ORTHANC_WAIT_SECONDS="${ORTHANC_WAIT_SECONDS:-300}"
# Report what would be uploaded and upload nothing.
SEED_DRY_RUN="${SEED_DRY_RUN:-false}"

# The credential never reaches a command line. `curl --user` would put it in argv, where
# any process in the container can read it from /proc, and SPCS surfaces argv in the
# service specification too. A 0600 netrc in the job's own tmpfs is the narrowest form
# curl accepts (secrets rule, trust/deploy/snowflake/README.md).
netrc="$(mktemp)"
chmod 600 "$netrc"
trap 'rm -f "$netrc"' EXIT
host="${ORTHANC_URL#*://}"
host="${host%%[:/]*}"
printf 'machine %s login %s password %s\n' "$host" "$ORTHANC_USERNAME" "$ORTHANC_PASSWORD" > "$netrc"

orthanc() {
  curl --silent --show-error --netrc-file "$netrc" "$@"
}

echo "Waiting up to ${ORTHANC_WAIT_SECONDS}s for Orthanc at ${ORTHANC_URL}"
deadline=$(( $(date +%s) + ORTHANC_WAIT_SECONDS ))
until orthanc --fail --output /dev/null "${ORTHANC_URL}/system"; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "ERROR: Orthanc did not answer /system within ${ORTHANC_WAIT_SECONDS}s." >&2
    exit 1
  fi
  sleep 5
done
echo "Orthanc is up."

dicom_root="${SEED_DIR}/dicom"
if [ ! -d "$dicom_root" ]; then
  echo "ERROR: ${dicom_root} does not exist. Put the DICOM on the SEED stage first:" >&2
  echo "       snow stage copy <local dicom dir> @FLIP_TRUST_<CODE>.STAGES.SEED/dicom/ --recursive" >&2
  exit 1
fi

# An unset PROJECTS seeds every project on the stage; a named one that is not there is a
# typo worth failing on rather than a silent no-op.
selected=()
if [ -n "$PROJECTS" ]; then
  for project in ${PROJECTS//,/ }; do
    if [ ! -d "${dicom_root}/${project}" ]; then
      echo "ERROR: project '${project}' is not on the SEED stage (${dicom_root}/${project})." >&2
      exit 1
    fi
    selected+=("${dicom_root}/${project}")
  done
else
  while IFS= read -r directory; do
    selected+=("$directory")
  done < <(find "$dicom_root" -mindepth 1 -maxdepth 1 -type d | sort)
fi

if [ "${#selected[@]}" -eq 0 ]; then
  echo "ERROR: no project directories under ${dicom_root}." >&2
  exit 1
fi

stored=0
already=0
failed=0
total=0

for project_dir in "${selected[@]}"; do
  project="$(basename "$project_dir")"
  count=$(find "$project_dir" -type f -name '*.dcm' | wc -l)
  echo "── ${project}: ${count} instance(s) on the stage"
  total=$(( total + count ))
  if [ "$SEED_DRY_RUN" = "true" ]; then
    continue
  fi
  while IFS= read -r instance; do
    # Orthanc answers 200 with {"Status":"Success"|"AlreadyStored", ...}; anything else
    # (a non-DICOM file, a rejected transfer syntax) is counted and reported, and the
    # run fails at the end rather than stopping on the first bad file.
    if ! body=$(orthanc --request POST --header 'Content-Type: application/dicom' \
          --data-binary "@${instance}" "${ORTHANC_URL}/instances" 2>&1); then
      echo "  FAILED ${instance}: ${body}" >&2
      failed=$(( failed + 1 ))
      continue
    fi
    case "$body" in
      *'"AlreadyStored"'*) already=$(( already + 1 )) ;;
      *'"Success"'*)       stored=$(( stored + 1 )) ;;
      *)
        echo "  FAILED ${instance}: ${body}" >&2
        failed=$(( failed + 1 ))
        ;;
    esac
  done < <(find "$project_dir" -type f -name '*.dcm' | sort)
done

if [ "$SEED_DRY_RUN" = "true" ]; then
  echo "DRY RUN: would upload ${total} instance(s) from ${#selected[@]} project(s)."
  exit 0
fi

echo "Seeded ${stored} new, ${already} already held, ${failed} failed, of ${total} instance(s)."
[ "$failed" -eq 0 ] || exit 1
[ "$total" -gt 0 ] || { echo "ERROR: the SEED stage held no .dcm files." >&2; exit 1; }
