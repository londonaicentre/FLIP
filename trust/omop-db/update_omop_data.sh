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

set -euo pipefail

# This script ensures that the local OMOP database data volumes are populated
# with the correct version of data as specified in the .data_version file in the repo.
# If the local data version does not match the desired version, it downloads the
# appropriate data archives from the public Hugging Face dataset and extracts them
# into the local volumes directory.
# NOTE this is only intended for use in development / test environments where real OMOP data is not available.

# These paths are relative to the location of this script
REPO_DATA_VERSION_FILE=".data_version"                        # committed in repo
VOLUMES_DIR="./volumes"                                       # local dir for omop-db volumes
LOCAL_DATA_VERSION_FILE="${VOLUMES_DIR}/.local_data_version"  # tracks local version

# Mock data is fetched anonymously over HTTPS from a public Hugging Face dataset
# (no AWS CLI or credentials required). The dataset is laid out per trust:
#   <repo>/resolve/<revision>/trust1/trust1_pgdata_<version>.tar
# Both the repo and revision can be overridden via the environment.
HF_TRUST_DATA_REPO="${HF_TRUST_DATA_REPO:-aicentreflip/trust-data}"
HF_TRUST_DATA_REVISION="${HF_TRUST_DATA_REVISION:-main}"
HF_BASE_URL="https://huggingface.co/datasets/${HF_TRUST_DATA_REPO}/resolve/${HF_TRUST_DATA_REVISION}"

# --- read desired data version from repo file ---
DATA_VERSION="$(tr -d ' \n\r\t' < "${REPO_DATA_VERSION_FILE}")"

mkdir -p "${VOLUMES_DIR}"

# Local version of OMOP data
LOCAL_VERSION=""
if [[ -f "${LOCAL_DATA_VERSION_FILE}" ]]; then
  LOCAL_VERSION="$(tr -d ' \n\r\t' < "${LOCAL_DATA_VERSION_FILE}" || true)"
fi

# If local version matches desired version, we're done - no need to download/extract again
if [[ "${LOCAL_VERSION}" == "${DATA_VERSION}" ]]; then
  echo "✅ OMOP data already up to date at version ${DATA_VERSION}."
  exit 0
fi

# If we reach here, we need to update the local OMOP data
if [[ -z "${LOCAL_VERSION}" ]]; then
  echo "❓ Local OMOP data version unknown. Will update to version ${DATA_VERSION} just to be safe."
else
  echo "🔄 Updating OMOP data: ${LOCAL_VERSION} -> ${DATA_VERSION}"
fi

# Archives are gzip-compressed tarballs named .tar on Hugging Face (the .gz is
# dropped from the name, not the content), grouped under per-trust dirs.
# tar auto-detects the gzip on extraction, so -xf (no -z) handles them.
TRUST1_ARCHIVE="trust1_pgdata_${DATA_VERSION}.tar"
TRUST2_ARCHIVE="trust2_pgdata_${DATA_VERSION}.tar"

HF_TRUST1_ARCHIVE="${HF_BASE_URL}/trust1/${TRUST1_ARCHIVE}"
HF_TRUST2_ARCHIVE="${HF_BASE_URL}/trust2/${TRUST2_ARCHIVE}"
LOCAL_TRUST1_ARCHIVE="${VOLUMES_DIR}/${TRUST1_ARCHIVE}"
LOCAL_TRUST2_ARCHIVE="${VOLUMES_DIR}/${TRUST2_ARCHIVE}"

# If the files do not exist locally, download them.
# -f: fail on HTTP errors, -S: show errors, -L: follow LFS redirects.
if [[ ! -f "${LOCAL_TRUST1_ARCHIVE}" ]]; then
  echo "📦 Downloading ${HF_TRUST1_ARCHIVE}"
  curl -fSL "${HF_TRUST1_ARCHIVE}" -o "${LOCAL_TRUST1_ARCHIVE}"
else
  echo "📦 ${LOCAL_TRUST1_ARCHIVE} already exists, skipping download"
fi

if [[ ! -f "${LOCAL_TRUST2_ARCHIVE}" ]]; then
  echo "📦 Downloading ${HF_TRUST2_ARCHIVE}"
  curl -fSL "${HF_TRUST2_ARCHIVE}" -o "${LOCAL_TRUST2_ARCHIVE}"
else
  echo "📦 ${LOCAL_TRUST2_ARCHIVE} already exists, skipping download"
fi

echo "🗑️ Removing existing db_data dirs..."
# The dirs are owned by the postgres container's uid, so removal needs sudo —
# but sudo prompts for a password in non-interactive runs. Only invoke it when
# there's actually something to delete (first-run case has no dirs yet).
for dir in "${VOLUMES_DIR}/Trust_1/db_data" "${VOLUMES_DIR}/Trust_2/db_data"; do
  if [[ -e "${dir}" ]]; then
    sudo rm -rf "${dir}"
  fi
done
mkdir -p "${VOLUMES_DIR}/Trust_1/db_data" "${VOLUMES_DIR}/Trust_2/db_data"

echo "📁 Extracting archives (will replace existing db_data dirs)..."
tar -xf "${LOCAL_TRUST1_ARCHIVE}" -C "${VOLUMES_DIR}/Trust_1/db_data"
tar -xf "${LOCAL_TRUST2_ARCHIVE}" -C "${VOLUMES_DIR}/Trust_2/db_data"

# Record the new local data version
echo "${DATA_VERSION}" > "${LOCAL_DATA_VERSION_FILE}"
echo "✅ Done. Local OMOP data version is now ${DATA_VERSION}"

# Delete the downloaded archives once extracted
if [[ "${CLEAN_AFTER_UPDATE:-False}" == "True" ]]; then
  rm -f "${LOCAL_TRUST1_ARCHIVE}" "${LOCAL_TRUST2_ARCHIVE}"
  echo "🧹 Cleaned up downloaded archives."
fi
