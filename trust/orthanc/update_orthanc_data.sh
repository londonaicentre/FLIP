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

# This script ensures that the local Orthanc storage dirs are populated
# with the correct version of mock DICOM data as specified in the .data_version
# file in the repo. If the local data version does not match the desired version,
# it downloads the appropriate data archives from the public Hugging Face dataset
# and extracts them into the local storage dirs.
# NOTE this is only intended for use in development / test environments where
# real DICOM data is not available.
#
# Set TRUST=1 or TRUST=2 to update only a single trust; defaults to "all" (both trusts).

# These paths are relative to the location of this script
REPO_DATA_VERSION_FILE=".data_version"  # committed in repo
VOLUMES_DIR="./volumes"                  # local dir for downloaded archives
# Pre-per-trust marker. Older versions of this script tracked a single shared
# version here; both trusts now use per-trust markers (.local_data_version_trust<N>).
LEGACY_DATA_VERSION_FILE="${VOLUMES_DIR}/.local_data_version"

# Per-trust storage dirs fall back to trust/-relative defaults when the caller
# (trust/orthanc/Makefile) hasn't sourced them from the kit files — the kit-file
# refactor on develop moved per-trust paths to the unsuffixed ORTHANC_STORAGE_DIR
# (e.g. ./orthanc/orthanc-storage-trust1 in trust/.env.<CODE>), so this script —
# shared across both trusts — keeps its own legacy-suffixed defaults to stay
# self-contained. Paths are resolved against trust/ by resolve_storage_dir below,
# so the defaults carry the orthanc/ prefix to match the compose mount.
: "${ORTHANC_STORAGE_DIR_TRUST_1:=orthanc/orthanc-storage-trust1}"
: "${ORTHANC_STORAGE_DIR_TRUST_2:=orthanc/orthanc-storage-trust2}"

# Mock data is fetched anonymously over HTTPS from a public Hugging Face dataset
# (no AWS CLI or credentials required). The dataset is laid out per trust:
#   <repo>/resolve/<revision>/trust1/trust1_orthanc_data_<version>.tar
# Both the repo and revision can be overridden via the environment.
HF_TRUST_DATA_REPO="${HF_TRUST_DATA_REPO:-aicentreflip/trust-data}"
HF_TRUST_DATA_REVISION="${HF_TRUST_DATA_REVISION:-main}"
HF_BASE_URL="https://huggingface.co/datasets/${HF_TRUST_DATA_REPO}/resolve/${HF_TRUST_DATA_REVISION}"

# TRUST controls which trust(s) to update: "1", "2", or "all" (default).
# Validate before env-var checks so TRUST=1 never requires TRUST_2's var.
TRUST="${TRUST:-all}"
if [[ "${TRUST}" != "1" && "${TRUST}" != "2" && "${TRUST}" != "all" ]]; then
  echo "❌ Invalid TRUST value '${TRUST}'. Must be 1, 2, or all." >&2
  exit 1
fi

# Resolve ORTHANC_STORAGE_DIR_TRUST_<N> against trust/ — the base Docker Compose
# uses (--project-directory ., invoked from trust/) — independent of this script's
# CWD (trust/orthanc).  Absolute paths are honored as-is.
TRUST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

resolve_storage_dir() {
  local p="$1"
  [[ "$p" = /* ]] && printf '%s\n' "$p" || printf '%s/%s\n' "${TRUST_DIR}" "$p"
}

# --- read desired data version from repo file ---
DATA_VERSION="$(tr -d ' \n\r\t' < "${REPO_DATA_VERSION_FILE}")"

mkdir -p "${VOLUMES_DIR}"

# Archives are gzip-compressed tarballs named .tar on Hugging Face (the .gz is
# dropped from the name, not the content), grouped under per-trust dirs.
# tar auto-detects the gzip on extraction, so -xf (no -z) handles them.

update_trust() {
  local trust_num="$1"
  local storage_dir_var="ORTHANC_STORAGE_DIR_TRUST_${trust_num}"
  local storage_dir; storage_dir="$(resolve_storage_dir "${!storage_dir_var}")"
  local local_version_file="${VOLUMES_DIR}/.local_data_version_trust${trust_num}"
  local archive="trust${trust_num}_orthanc_data_${DATA_VERSION}.tar"
  local hf_url="${HF_BASE_URL}/trust${trust_num}/${archive}"
  local local_archive="${VOLUMES_DIR}/${archive}"

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
    echo "✅ Orthanc data for Trust ${trust_num} already up to date at version ${DATA_VERSION}."
    # Ensure permissions even for an already-extracted dir (may have been
    # extracted by an older version of this script that didn't set them).
    chmod -R 777 "${storage_dir}" 2>/dev/null || true
    return
  fi

  if [[ -z "${local_version}" ]]; then
    echo "❓ Local Orthanc data version for Trust ${trust_num} unknown. Will update to version ${DATA_VERSION} just to be safe."
  else
    echo "🔄 Updating Orthanc data for Trust ${trust_num}: ${local_version} -> ${DATA_VERSION}"
  fi

  if [[ ! -f "${local_archive}" ]]; then
    echo "📦 Downloading ${hf_url}"
    curl -fSL "${hf_url}" -o "${local_archive}"
  else
    echo "📦 ${local_archive} already exists, skipping download"
  fi

  echo "🗑️  Removing existing orthanc storage dir for Trust ${trust_num}..."
  if [[ -e "${storage_dir}" ]]; then
    # Orthanc (uid 999) creates uid-999-owned subdirs after first run, so a plain rm
    # by the host user can't traverse them. Use sudo to force removal.
    sudo rm -rf "${storage_dir}"
  fi
  mkdir -p "${storage_dir}"

  echo "📁 Extracting archive for Trust ${trust_num}..."
  tar -xf "${local_archive}" -C "${storage_dir}"

  # Ensure the Orthanc container user (uid 999) can write the storage dir.
  # The compose applies cap_drop: ALL (no DAC_OVERRIDE), so Orthanc cannot
  # write files owned by the host uid (1000) under mode 755 — the SQLite DB
  # that Orthanc creates at startup would fail with "Unable to open the
  # database" (code 1002). Making the tree world-writable is the simplest
  # fix for dev-only mock data.
  chmod -R 777 "${storage_dir}"

  echo "${DATA_VERSION}" > "${local_version_file}"
  echo "✅ Done. Local Orthanc data for Trust ${trust_num} is now at version ${DATA_VERSION}"

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
