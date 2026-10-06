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

# Checks or fills a local cache of the third-party XNAT artifacts listed in artifacts.manifest.
#
# Usage: xnat_artifacts.sh <check|fetch> <war|plugin> <dest_dir>
#
#   check  Verifies that <dest_dir> holds exactly the manifest's files of that kind, each with its
#          pinned SHA-256. Never downloads anything, so `make up` can run it on a host with no
#          internet route and no credentials. Exits 3 when the cache needs preparing.
#   fetch  Fills <dest_dir>: keeps every file that already matches, downloads the rest from the
#          upstream URL in the manifest (or copies them from $ARTIFACTS_DIR when set, for a host
#          that cannot reach upstream), verifies each checksum, and for plugins removes any jar
#          the manifest does not list. Ends with a check.
#
# Environment:
#   ARTIFACTS_DIR            A directory holding the artifacts under their upstream file names.
#                            Used instead of downloading.
#   XNAT_ARTIFACTS_MANIFEST  The manifest to read (default: ../artifacts.manifest beside this script).
#
# The checksum is the whole contract. A file that is present, a valid zip and the right name but
# not the pinned bytes is a different build of that plugin, and the dev stack bind-mounts this
# directory straight into the running XNAT, so it is treated as missing and replaced. An extra jar
# is a failure for the same reason: XNAT loads every jar in the directory, so a stale version
# beside the new one loads both.

set -euo pipefail

MODE="${1:-}"
KIND="${2:-}"
DEST_DIR="${3:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${XNAT_ARTIFACTS_MANIFEST:-${SCRIPT_DIR}/../artifacts.manifest}"
PREPARE_HINT="make -C trust prepare-artifacts"

usage() {
  echo "Usage: $0 <check|fetch> <war|plugin> <dest_dir>" >&2
  exit 2
}

[[ "${MODE}" == "check" || "${MODE}" == "fetch" ]] || usage
[[ "${KIND}" == "war" || "${KIND}" == "plugin" ]] || usage
[[ -n "${DEST_DIR}" ]] || usage
[[ -r "${MANIFEST}" ]] || { echo "❌ Artifact manifest not found: ${MANIFEST}" >&2; exit 2; }

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# Parallel arrays of the manifest entries of this kind: file name, checksum, URL.
names=()
sums=()
urls=()
while read -r kind sum url _; do
  [[ -z "${kind}" || "${kind}" == \#* ]] && continue
  [[ "${kind}" == "${KIND}" ]] || continue
  if [[ ! "${sum}" =~ ^[0-9a-f]{64}$ || -z "${url}" ]]; then
    echo "❌ Malformed ${KIND} line in ${MANIFEST}: ${kind} ${sum} ${url}" >&2
    exit 2
  fi
  names+=("${url##*/}")
  sums+=("${sum}")
  urls+=("${url}")
done <"${MANIFEST}"

if [[ ${#names[@]} -eq 0 ]]; then
  echo "❌ ${MANIFEST} lists no ${KIND} artifacts." >&2
  exit 2
fi

is_listed() {
  local candidate="$1" name
  for name in "${names[@]}"; do
    [[ "${name}" == "${candidate}" ]] && return 0
  done
  return 1
}

matches() {
  local i="$1"
  [[ -f "${DEST_DIR}/${names[i]}" && "$(sha256_of "${DEST_DIR}/${names[i]}")" == "${sums[i]}" ]]
}

# Jars in the cache that the manifest does not list. Only plugins: the WAR directory is a Docker
# build context that also holds WARs of other XNAT versions a developer built earlier, and only the
# named one is copied into the image.
unlisted_jars() {
  local jar
  [[ "${KIND}" == "plugin" ]] || return 0
  for jar in "${DEST_DIR}"/*.jar; do
    [[ -e "${jar}" ]] || continue
    is_listed "${jar##*/}" || printf '%s\n' "${jar##*/}"
  done
}

check() {
  local i problems=()
  for i in "${!names[@]}"; do
    if [[ ! -f "${DEST_DIR}/${names[i]}" ]]; then
      problems+=("missing ${names[i]}")
    elif ! matches "${i}"; then
      problems+=("checksum mismatch ${names[i]}")
    fi
  done
  while read -r extra; do
    [[ -n "${extra}" ]] && problems+=("not in the manifest ${extra}")
  done < <(unlisted_jars)

  if [[ ${#problems[@]} -gt 0 ]]; then
    echo "❌ The XNAT ${KIND} cache in ${DEST_DIR} is not ready:"
    printf '     - %s\n' "${problems[@]}"
    echo "   Prepare it with: ${PREPARE_HINT}"
    echo "   (downloads from the public upstream URLs in trust/xnat/artifacts.manifest; with no"
    echo "   internet route, put the files in a directory and add ARTIFACTS_DIR=<dir>)"
    return 3
  fi
  echo "✅ XNAT ${KIND} cache matches the manifest (${#names[@]} file(s), checksums verified)."
}

fetch_one() {
  local i="$1" part="${DEST_DIR}/.${names[i]}.part" actual
  rm -f "${part}"
  if [[ -n "${ARTIFACTS_DIR:-}" ]]; then
    if [[ ! -f "${ARTIFACTS_DIR}/${names[i]}" ]]; then
      echo "❌ ${names[i]} is not in ARTIFACTS_DIR (${ARTIFACTS_DIR}). Its upstream URL: ${urls[i]}" >&2
      return 1
    fi
    echo "📦 Copying ${names[i]} from ${ARTIFACTS_DIR}..."
    cp "${ARTIFACTS_DIR}/${names[i]}" "${part}"
  else
    command -v curl >/dev/null 2>&1 || { echo "❌ curl is required to download ${names[i]}." >&2; return 1; }
    echo "⬇️  Downloading ${names[i]}..."
    if ! curl -fsSL --retry 3 --retry-delay 2 -o "${part}" "${urls[i]}"; then
      rm -f "${part}"
      echo "❌ Download failed: ${urls[i]}" >&2
      return 1
    fi
  fi
  actual="$(sha256_of "${part}")"
  if [[ "${actual}" != "${sums[i]}" ]]; then
    rm -f "${part}"
    echo "❌ ${names[i]} has SHA-256 ${actual}, but the manifest pins ${sums[i]}. Nothing was installed." >&2
    return 1
  fi
  mv -f "${part}" "${DEST_DIR}/${names[i]}"
}

fetch() {
  local i extra
  mkdir -p "${DEST_DIR}"
  for i in "${!names[@]}"; do
    if matches "${i}"; then
      echo "✅ ${names[i]} already present."
    else
      [[ -e "${DEST_DIR}/${names[i]}" ]] && echo "⚠️  Replacing ${names[i]}: its checksum does not match the manifest."
      fetch_one "${i}"
    fi
  done
  while read -r extra; do
    [[ -n "${extra}" ]] || continue
    echo "🧹 Removing ${extra}: not in the manifest."
    rm -f "${DEST_DIR:?}/${extra}"
  done < <(unlisted_jars)
  # Left by the S3-synced cache this script replaced; the checksums now carry what it recorded.
  rm -f "${DEST_DIR}/.s3-prefix"
  check
}

"${MODE}"
