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
# exactly what `make -C trust ensure-seeded` runs on a dev host — the same two loaders, the same
# flags — from an image that has python and uv and nothing else (ghcr.io/astral-sh/uv).
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
set -euo pipefail

WORK_DIR="${WORK_DIR:-/work}"
FLIP_REF="${FLIP_REF:-develop}"
HF_TRUST_DATA_REPO="${HF_TRUST_DATA_REPO:-aicentreflip/trust-data}"
PROJECTS="${PROJECTS:-cxr_project spleen_project}"
NUM_TRUSTS="${NUM_TRUSTS:-2}"
VOCAB_DICOM_BUNDLE="${VOCAB_DICOM_BUNDLE:-vocab_dicom_paulnagy_20260109}"
TOOLS_SPEC="${TOOLS_SPEC:-omop-db-tools @ https://github.com/londonaicentre/FLIP/archive/${FLIP_REF}.tar.gz#subdirectory=trust/omop-db}"
SEEDER="${SEEDER:-https://raw.githubusercontent.com/londonaicentre/FLIP/${FLIP_REF}/trust/orthanc/seed_orthanc.py}"
: "${TRUST_DATA_VERSION:?the dataset tag to seed at (trust/.data_version)}"
: "${SOURCE_TRUST:?the source_trust partition this trust receives}"

mkdir -p "${WORK_DIR}"
export UV_CACHE_DIR="${WORK_DIR}/uv-cache" UV_LINK_MODE=copy

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
  python3 -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' "$1" "$2"
}

echo "seed_trust: source_trust ${SOURCE_TRUST} of ${NUM_TRUSTS}; projects [${PROJECTS}]; data version ${TRUST_DATA_VERSION}; tools ${TOOLS_SPEC}"

if [ "${SEED_OMOP:-true}" = "true" ]; then
  wait_tcp "${OMOP_DB_HOST:?}" "${OMOP_DB_PORT:?}"
  # shellcheck disable=SC2086
  tools python -m omop_db_tools.dataset fetch --revision "${TRUST_DATA_VERSION}" --dest "${WORK_DIR}/canonical" --projects ${PROJECTS}
  VOCAB="${WORK_DIR}/${VOCAB_DICOM_BUNDLE}"
  [ -f "${VOCAB}.zip" ] || [ -d "${VOCAB}" ] || fetch "https://huggingface.co/datasets/${HF_TRUST_DATA_REPO}/resolve/${TRUST_DATA_VERSION}/omop-vocab/${VOCAB_DICOM_BUNDLE}.zip" "${VOCAB}.zip"
  tools python -m omop_db_tools.load_dicom_vocab --vocab-dir "${VOCAB}" --skip-if-loaded
  # shellcheck disable=SC2086
  tools python -m omop_db_tools.import_tables --trust-index "${SOURCE_TRUST}" --num-trusts "${NUM_TRUSTS}" \
    --partition source_trust --clean projects --projects ${PROJECTS} --data-dir "${WORK_DIR}/canonical"
else
  echo "seed_trust: OMOP half disabled (SEED_OMOP=${SEED_OMOP:-})"
fi

if [ "${SEED_ORTHANC:-false}" = "true" ]; then
  if [ -z "${ORTHANC_USERNAME:-}" ]; then
    # Orthanc's own user map (as the chart holds it); the seeder wants one login.
    eval "$(python3 -c 'import json, os, shlex; u, p = next(iter(json.loads(os.environ["ORTHANC_REGISTERED_USERS"]).items())); print(f"export ORTHANC_USERNAME={shlex.quote(u)} ORTHANC_PASSWORD={shlex.quote(p)}")')"
  fi
  python3 - <<'PY'
import base64, os, sys, time, urllib.request
url = os.environ["ORTHANC_URL"] + "/system"
auth = base64.b64encode(f'{os.environ["ORTHANC_USERNAME"]}:{os.environ["ORTHANC_PASSWORD"]}'.encode()).decode()
for _ in range(60):
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Basic " + auth}), timeout=5).close(); sys.exit(0)
    except Exception:
        time.sleep(5)
sys.exit(f"{url} not reachable after 5 minutes")
PY
  # shellcheck disable=SC2086
  uv run --no-project "${SEEDER}" --trust-index "${SOURCE_TRUST}" --projects ${PROJECTS} \
    --revision "${TRUST_DATA_VERSION}" --orthanc-url "${ORTHANC_URL:?}" --cache-dir "${WORK_DIR}/dicom"
else
  echo "seed_trust: Orthanc half disabled (SEED_ORTHANC=${SEED_ORTHANC:-})"
fi
echo "seed_trust: done"
