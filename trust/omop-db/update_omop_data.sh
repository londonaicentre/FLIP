#!/usr/bin/env bash
#
# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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
# appropriate data archives from the specified S3 bucket and extracts them into
# the local volumes directory.
# NOTE this is only intended for use in development / test environments where real OMOP data is not available.

# These paths are relative to the location of this script
REPO_DATA_VERSION_FILE=".data_version"                        # committed in repo
VOLUMES_DIR="./volumes"                                       # local dir for omop-db volumes
LOCAL_DATA_VERSION_FILE="${VOLUMES_DIR}/.local_data_version"  # tracks local version

# Required env var: AICENTRE_BUCKET_NAME
: "${AICENTRE_BUCKET_NAME:?AICENTRE_BUCKET_NAME is required}"

S3_OMOP_PREFIX="s3://${AICENTRE_BUCKET_NAME}/omop"

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

# Download the appropriate .tar.gz files from S3
TRUST1_ARCHIVE="trust1_pgdata_${DATA_VERSION}.tar.gz"
TRUST2_ARCHIVE="trust2_pgdata_${DATA_VERSION}.tar.gz"

S3_TRUST1_ARCHIVE="${S3_OMOP_PREFIX}/${TRUST1_ARCHIVE}"
S3_TRUST2_ARCHIVE="${S3_OMOP_PREFIX}/${TRUST2_ARCHIVE}"
LOCAL_TRUST1_ARCHIVE="${VOLUMES_DIR}/${TRUST1_ARCHIVE}"
LOCAL_TRUST2_ARCHIVE="${VOLUMES_DIR}/${TRUST2_ARCHIVE}"

# If the files do not exist locally, download them
if [[ ! -f "${LOCAL_TRUST1_ARCHIVE}" ]]; then
  echo "📦 Downloading ${S3_TRUST1_ARCHIVE}"
  aws s3 cp "${S3_TRUST1_ARCHIVE}" "${LOCAL_TRUST1_ARCHIVE}"
else
  echo "📦 ${LOCAL_TRUST1_ARCHIVE} already exists, skipping download"
fi

if [[ ! -f "${LOCAL_TRUST2_ARCHIVE}" ]]; then
  echo "📦 Downloading ${S3_TRUST2_ARCHIVE}"
  aws s3 cp "${S3_TRUST2_ARCHIVE}" "${LOCAL_TRUST2_ARCHIVE}"
else
  echo "📦 ${LOCAL_TRUST2_ARCHIVE} already exists, skipping download"
fi

echo "🗑️ Removing existing db_data dirs..."
sudo rm -rf "${VOLUMES_DIR}/trust1/db_data" "${VOLUMES_DIR}/trust2/db_data"
mkdir -p "${VOLUMES_DIR}/trust1/db_data" "${VOLUMES_DIR}/trust2/db_data"

echo "📁 Extracting .tar.gz files (will replace existing db_data dirs)..."
tar -xzf "${LOCAL_TRUST1_ARCHIVE}" -C "${VOLUMES_DIR}/trust1/db_data"
tar -xzf "${LOCAL_TRUST2_ARCHIVE}" -C "${VOLUMES_DIR}/trust2/db_data"

# Record the new local data version
echo "${DATA_VERSION}" > "${LOCAL_DATA_VERSION_FILE}"
echo "✅ Done. Local OMOP data version is now ${DATA_VERSION}"

# Delete the downloaded archives once extracted
if [[ "${CLEAN_AFTER_UPDATE:-False}" == "True" ]]; then
  rm -f "${LOCAL_TRUST1_ARCHIVE}" "${LOCAL_TRUST2_ARCHIVE}"
  echo "🧹 Cleaned up downloaded archives."
fi