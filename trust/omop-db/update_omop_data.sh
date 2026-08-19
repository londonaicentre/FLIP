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
#
# Set TRUST=1 or TRUST=2 to update only a single trust; defaults to "all" (both trusts).

# These paths are relative to the location of this script
REPO_DATA_VERSION_FILE=".data_version"  # committed in repo
VOLUMES_DIR="./volumes"                  # local dir for omop-db volumes
# Pre-per-trust marker. Older versions of this script tracked a single shared
# version here; both trusts now use per-trust markers (.local_data_version_trust<N>).
LEGACY_DATA_VERSION_FILE="${VOLUMES_DIR}/.local_data_version"

# Mock data is fetched anonymously over HTTPS from a public Hugging Face dataset
# (no AWS CLI or credentials required). The dataset is laid out per trust:
#   <repo>/resolve/<revision>/trust1/trust1_pgdata_<version>.tar
# Both the repo and revision can be overridden via the environment.
HF_TRUST_DATA_REPO="${HF_TRUST_DATA_REPO:-aicentreflip/trust-data}"
HF_TRUST_DATA_REVISION="${HF_TRUST_DATA_REVISION:-main}"
HF_BASE_URL="https://huggingface.co/datasets/${HF_TRUST_DATA_REPO}/resolve/${HF_TRUST_DATA_REVISION}"

# TRUST controls which trust(s) to update: "1", "2", or "all" (default).
TRUST="${TRUST:-all}"
if [[ "${TRUST}" != "1" && "${TRUST}" != "2" && "${TRUST}" != "all" ]]; then
  echo "❌ Invalid TRUST value '${TRUST}'. Must be 1, 2, or all." >&2
  exit 1
fi

# --- read desired data version from repo file ---
DATA_VERSION="$(tr -d ' \n\r\t' < "${REPO_DATA_VERSION_FILE}")"

mkdir -p "${VOLUMES_DIR}"

# Archives are gzip-compressed tarballs named .tar on Hugging Face (the .gz is
# dropped from the name, not the content), grouped under per-trust dirs.
# tar auto-detects the gzip on extraction, so -xf (no -z) handles them.

update_trust() {
  local trust_num="$1"
  local local_version_file="${VOLUMES_DIR}/.local_data_version_trust${trust_num}"
  local archive="trust${trust_num}_pgdata_${DATA_VERSION}.tar"
  local hf_url="${HF_BASE_URL}/trust${trust_num}/${archive}"
  local local_archive="${VOLUMES_DIR}/${archive}"
  local dest_dir="${VOLUMES_DIR}/Trust_${trust_num}/db_data"

  local local_version=""
  if [[ -f "${local_version_file}" ]]; then
    local_version="$(tr -d ' \n\r\t' < "${local_version_file}" || true)"
  elif [[ -f "${LEGACY_DATA_VERSION_FILE}" ]]; then
    # Migrate from the pre-per-trust shared marker: an existing install already
    # holds this version for both trusts, so adopt it instead of forcing a
    # needless re-download. The legacy file is removed once both trusts have
    # migrated (see cleanup after the update_trust calls).
    local_version="$(tr -d ' \n\r\t' < "${LEGACY_DATA_VERSION_FILE}" || true)"
    echo "${local_version}" > "${local_version_file}"
  fi

  if [[ "${local_version}" == "${DATA_VERSION}" ]]; then
    echo "✅ OMOP data for Trust ${trust_num} already up to date at version ${DATA_VERSION}."
    return
  fi

  if [[ -z "${local_version}" ]]; then
    echo "❓ Local OMOP data version for Trust ${trust_num} unknown. Will update to version ${DATA_VERSION} just to be safe."
  else
    echo "🔄 Updating OMOP data for Trust ${trust_num}: ${local_version} -> ${DATA_VERSION}"
  fi

  if [[ ! -f "${local_archive}" ]]; then
    echo "📦 Downloading ${hf_url}"
    curl -fSL "${hf_url}" -o "${local_archive}"
  else
    echo "📦 ${local_archive} already exists, skipping download"
  fi

  echo "🗑️  Removing existing db_data dir for Trust ${trust_num}..."
  # The dir may be owned by the postgres container's uid, in which case removal
  # needs sudo. But sudo prompts for a password in non-interactive runs, and it
  # is frequently NOT needed: on Docker Desktop (macOS/Windows) the virtiofs
  # bind mount maps ownership onto the invoking host user, so a plain rm works.
  # Try unprivileged first and escalate only if that actually fails.
  if [[ -e "${dest_dir}" ]]; then
    if ! rm -rf "${dest_dir}" 2>/dev/null; then
      echo "   (root-owned by the container; escalating to sudo)"
      sudo rm -rf "${dest_dir}"
    fi
  fi
  mkdir -p "${dest_dir}"

  echo "📁 Extracting archive for Trust ${trust_num}..."
  tar -xf "${local_archive}" -C "${dest_dir}"

  echo "${DATA_VERSION}" > "${local_version_file}"
  echo "✅ Done. Local OMOP data for Trust ${trust_num} is now at version ${DATA_VERSION}"

  if [[ "${CLEAN_AFTER_UPDATE:-False}" == "True" ]]; then
    rm -f "${local_archive}"
    echo "🧹 Cleaned up downloaded archive for Trust ${trust_num}."
  fi
}

if [[ "${TRUST}" == "1" || "${TRUST}" == "all" ]]; then
  update_trust 1
fi

if [[ "${TRUST}" == "2" || "${TRUST}" == "all" ]]; then
  update_trust 2
fi

# Remove the orphaned legacy shared marker once both per-trust markers exist, so
# it doesn't linger invisibly after the migration. Only delete when both trusts
# have a marker — a single-trust run must not strand the other trust's migration.
if [[ -f "${LEGACY_DATA_VERSION_FILE}" \
   && -f "${VOLUMES_DIR}/.local_data_version_trust1" \
   && -f "${VOLUMES_DIR}/.local_data_version_trust2" ]]; then
  rm -f "${LEGACY_DATA_VERSION_FILE}"
  echo "🧹 Removed orphaned legacy version file ${LEGACY_DATA_VERSION_FILE}."
fi
