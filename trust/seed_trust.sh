#!/usr/bin/env bash
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
# Seed one trust from the canonical dataset (FLIP#1101/#1187): its `source_trust` slice of each
# listed project's OMOP rows into a reachable omop-db (the DICOM vocabulary first, skipped when
# present) and the matching studies into a reachable Orthanc over REST. The one seeding
# procedure shared by the Kubernetes chart's trust-seed hook and the EC2 playbook, so both run
# what `make -C trust ensure-seeded` runs on a dev host — the same two loaders and the same
# empty-store rule — from an image that has python and uv and nothing else (ghcr.io/astral-sh/uv).
#
# The loaders are the repository's own: trust/omop-db's omop_db_tools and trust/orthanc/
# seed_orthanc.py. Where they come from is the caller's choice —
#   TOOLS_SPEC   a uv requirement for omop-db-tools; default: the FLIP source archive at FLIP_REF
#                (a tarball, since the slim image has no git); EC2 passes a local checkout path.
#   SEEDER       the seed_orthanc.py to run; default: the raw file at FLIP_REF; EC2 a local copy.
# Everything else is plain environment:
#   OMOP_DB_HOST OMOP_DB_PORT OMOP_POSTGRES_USER OMOP_POSTGRES_PASSWORD OMOP_POSTGRES_DB
#   ORTHANC_URL and either ORTHANC_USERNAME + ORTHANC_PASSWORD or ORTHANC_REGISTERED_USERS (the
#   JSON user map Orthanc itself reads; its first entry is used)
#   TRUST_DATA_VERSION (the dataset tag) HF_TRUST_DATA_REPO PROJECTS SOURCE_TRUST NUM_TRUSTS
#   SEED_OMOP / SEED_ORTHANC ("true" to run that half) VOCAB_DICOM_BUNDLE WORK_DIR (scratch)
#   FORCE_DICOM_VOCAB ("1" to reload the DICOM vocabulary over one that reports itself present;
#   on a database that holds data, that reload is all it does)
#   RESEED ("true" to seed a store that already holds data, Orthanc clearing the listed projects'
#   studies first: how a trust moves to a new data version)
#   UV_EXCLUDE_NEWER (the dependency cooldown; default the repository's "3 days")
#
# Each half seeds only a store that is still empty (FLIP#2111), the rule `make -C trust
# ensure-seeded` applies on a Compose host: OMOP with no omop.image_occurrence row (persons alone
# may be Synthea's), Orthanc with no study. So a re-run — every Helm upgrade runs the hook — fetches
# and loads nothing on a seeded trust, and a data-version bump is an explicit RESEED=true. A run that
# fails part-way leaves a store that is no longer empty, which a re-run would then skip: the failure
# is the signal (the chart's hook does not retry), and RESEED=true is the way to finish the load.
set -euo pipefail

WORK_DIR="${WORK_DIR:-/work}"
FLIP_REF="${FLIP_REF:-develop}"
HF_TRUST_DATA_REPO="${HF_TRUST_DATA_REPO:-aicentreflip/trust-data}"
# Only projects the dataset publishes BOTH tables and a DICOM set for: one list drives both
# halves, and since FLIP#1221 spleen and brain_mri publish tables only (their DICOMs are
# regenerated locally, which nothing in a cluster or on an EC2 host can do).
PROJECTS="${PROJECTS:-cxr_project}"
NUM_TRUSTS="${NUM_TRUSTS:-2}"
VOCAB_DICOM_BUNDLE="${VOCAB_DICOM_BUNDLE:-vocab_dicom_paulnagy_20260109}"
TOOLS_SPEC="${TOOLS_SPEC:-omop-db-tools @ https://github.com/londonaicentre/FLIP/archive/${FLIP_REF}.tar.gz#subdirectory=trust/omop-db}"
SEEDER="${SEEDER:-https://raw.githubusercontent.com/londonaicentre/FLIP/${FLIP_REF}/trust/orthanc/seed_orthanc.py}"
: "${TRUST_DATA_VERSION:?the dataset tag to seed at (trust/.data_version)}"
: "${SOURCE_TRUST:?the source_trust partition this trust receives}"

mkdir -p "${WORK_DIR}"
export UV_CACHE_DIR="${WORK_DIR}/uv-cache" UV_LINK_MODE=copy
# Both installs below resolve with no lock, and uv reads no [tool.uv] for a --with requirement or
# a fetched script, so the 72-hour cooldown (CONTRIBUTING "Dependency cooldown") is applied here.
export UV_EXCLUDE_NEWER="${UV_EXCLUDE_NEWER:-3 days}"

tools() { uv run --no-project --with "${TOOLS_SPEC}" "$@"; }

wait_tcp() {  # $1 host $2 port — omop-db answers over TCP only once its init scripts are done
  python3 - "$1" "$2" <<'PY'
import socket, sys, time
host, port = sys.argv[1], int(sys.argv[2])
for _ in range(120):
    try:
        socket.create_connection((host, port), timeout=3).close(); sys.exit(0)
    except OSError:
        time.sleep(5)
sys.exit(f"{host}:{port} not reachable after 10 minutes")
PY
}

fetch() {  # $1 url $2 dest
  python3 - "$1" "$2" <<'PY'
import os, sys, time, urllib.request

url, dest = sys.argv[1], sys.argv[2]
# Staged through .part and renamed only on success: the caller gates the fetch on the
# destination existing, so a stream cut short would otherwise be taken for a complete bundle
# by every later run — and only the Kubernetes Job gets a fresh scratch dir to escape that.
tmp = f"{dest}.part"
for attempt in range(1, 4):
    try:
        urllib.request.urlretrieve(url, tmp)
        os.replace(tmp, dest)
        break
    except Exception as error:  # a fetch over the public internet: transient until it is not
        if attempt == 3:
            raise
        print(f"   retry {attempt}/3 after {type(error).__name__}: {error}", flush=True)
        time.sleep(attempt * 5)
PY
}

# The DICOM vocabulary, loaded first so the projects' imaging concepts resolve. The loader's "already
# loaded" signal is a single scaffolding concept, committed before the concepts and relationships it
# precedes — so a crash, an OOM or an evicted pod between those loads leaves a database that reports
# itself loaded while holding an incomplete vocabulary, and every later run skips straight past it.
# FORCE_DICOM_VOCAB=1 is the way back, the same escape `make -C trust/omop-db load-dicom-vocab` has;
# it reloads, duplicating concept_relationship rows (no unique key there), the lesser of the two states.
dicom_vocab() {
  local vocab="${WORK_DIR}/${VOCAB_DICOM_BUNDLE}" mode=--skip-if-loaded
  [ -f "${vocab}.zip" ] || [ -d "${vocab}" ] || fetch "https://huggingface.co/datasets/${HF_TRUST_DATA_REPO}/resolve/${TRUST_DATA_VERSION}/omop-vocab/${VOCAB_DICOM_BUNDLE}.zip" "${vocab}.zip"
  [ "${FORCE_DICOM_VOCAB:-}" = "1" ] && mode=--force
  tools python -m omop_db_tools.load_dicom_vocab --vocab-dir "${vocab}" "${mode}"
}

echo "seed_trust: source_trust ${SOURCE_TRUST} of ${NUM_TRUSTS}; projects [${PROJECTS}]; data version ${TRUST_DATA_VERSION}; tools ${TOOLS_SPEC}"

RESEED="${RESEED:-false}"

if [ "${SEED_OMOP:-true}" != "true" ]; then
  echo "seed_trust: OMOP half disabled (SEED_OMOP=${SEED_OMOP:-})"
else
  wait_tcp "${OMOP_DB_HOST:?}" "${OMOP_DB_PORT:?}"
  OMOP_HELD="$(tools python -m omop_db_tools.import_tables --trust-index "${SOURCE_TRUST}" --holds-data)"
  case "${OMOP_HELD}" in
    yes | no) ;;
    *) echo "seed_trust: the OMOP probe answered '${OMOP_HELD}', not yes or no: stopping" >&2; exit 1 ;;
  esac
  if [ "${OMOP_HELD}" = yes ] && [ "${RESEED}" != "true" ]; then
    if [ "${FORCE_DICOM_VOCAB:-}" = "1" ]; then
      echo "seed_trust: OMOP holds data; reloading only the DICOM vocabulary (FORCE_DICOM_VOCAB=1)"
      dicom_vocab
    else
      echo "seed_trust: OMOP already holds data, left as it is (RESEED=true re-seeds it)"
    fi
  else
    # shellcheck disable=SC2086
    tools python -m omop_db_tools.dataset fetch --revision "${TRUST_DATA_VERSION}" --dest "${WORK_DIR}/canonical" --projects ${PROJECTS}
    dicom_vocab
    # shellcheck disable=SC2086
    tools python -m omop_db_tools.import_tables --trust-index "${SOURCE_TRUST}" --num-trusts "${NUM_TRUSTS}" \
      --partition source_trust --clean projects --projects ${PROJECTS} --data-dir "${WORK_DIR}/canonical"
  fi
fi

if [ "${SEED_ORTHANC:-false}" = "true" ]; then
  if [ -z "${ORTHANC_USERNAME:-}" ]; then
    # Orthanc's own user map (as the chart holds it); the seeder wants one login.
    eval "$(python3 -c 'import json, os, shlex; u, p = next(iter(json.loads(os.environ["ORTHANC_REGISTERED_USERS"]).items())); print(f"export ORTHANC_USERNAME={shlex.quote(u)} ORTHANC_PASSWORD={shlex.quote(p)}")')"
  fi
  # Waits for Orthanc, then prints how many studies it holds.
  STUDIES="$(python3 - <<'PY'
import base64, json, os, sys, time, urllib.error, urllib.request
url = os.environ["ORTHANC_URL"] + "/statistics"
auth = base64.b64encode(f'{os.environ["ORTHANC_USERNAME"]}:{os.environ["ORTHANC_PASSWORD"]}'.encode()).decode()
for _ in range(60):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Basic " + auth}), timeout=5) as r:
            print(int(json.load(r).get("CountStudies", 0))); sys.exit(0)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):  # a wrong login, not an Orthanc still starting
            sys.exit(f"{url} refused the login ({error.code}): check ORTHANC_USERNAME / ORTHANC_REGISTERED_USERS")
        time.sleep(5)
    except Exception:
        time.sleep(5)
sys.exit(f"{url} not reachable after 5 minutes")
PY
)"
  if [ "${STUDIES}" -gt 0 ] && [ "${RESEED}" != "true" ]; then
    echo "seed_trust: Orthanc already holds ${STUDIES} studies, left as they are (RESEED=true re-seeds it)"
  else
    CLEAR=""
    [ "${RESEED}" = "true" ] && CLEAR=--clear-projects
    # shellcheck disable=SC2086
    uv run --no-project "${SEEDER}" --trust-index "${SOURCE_TRUST}" --projects ${PROJECTS} \
      --revision "${TRUST_DATA_VERSION}" --orthanc-url "${ORTHANC_URL:?}" --cache-dir "${WORK_DIR}/dicom" ${CLEAR}
  fi
else
  echo "seed_trust: Orthanc half disabled (SEED_ORTHANC=${SEED_ORTHANC:-})"
fi
echo "seed_trust: done"
