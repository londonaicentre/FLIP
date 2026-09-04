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

# Script to ensure required XNAT plugins are present in the local plugin directory.
# If any required plugin families are missing, it attempts to sync them from the specified S3 bucket.
# If they are already present, it skips the S3 sync command, so it does not rely on S3 access if the plugins are already available locally.
# Usage: ./ensure_plugins.sh <plugin_dir> <s3_bucket> [s3_prefix]
# s3_prefix defaults to the legacy flat layout (xnat/plugins); callers pass the version-keyed
# prefix (xnat-<version>/plugins) so each XNAT version keeps its own plugin roster in S3 and
# branches on different XNAT versions can coexist.
#
# The presence check below matches plugin *families* by filename prefix, not versions — the
# version roster is whatever the S3 prefix holds. So the prefix each sync came from is recorded
# in a stamp file, and a mismatch (or no stamp: jars cached before the version-keyed layout)
# forces a re-sync whose --delete clears the stale versions. Without this, a checkout that
# changes XNAT version would accept a developer's previously-cached jars and silently bake the
# old, incompatible plugin versions into the new image.

set -euo pipefail

PLUGIN_DIR="${1:-}"
S3_BUCKET="${2:-}"
S3_PREFIX="${3:-xnat/plugins}"

if [[ -z "${PLUGIN_DIR}" || -z "${S3_BUCKET}" ]]; then
  echo "Usage: $0 <plugin_dir> <s3_bucket> [s3_prefix]"
  exit 1
fi

STAMP_FILE="${PLUGIN_DIR}/.s3-prefix"

# The OHIF viewer is opt-in, via XNAT_OHIF_VIEWER.
#
# It was previously excluded outright: FLIP used XNAT purely as a DICOM store for FL training and
# never opened the viewer, while the plugin's per-session metadata-rebuild event listener was the
# dominant load on XNAT's Reactor EventBus and was implicated in the back-pressure livelock that
# wedged bulk cohort imports (FLIP#662).
#
# Both halves of that reasoning have moved. Digital pathology gives the viewer a real purpose --
# XNAT-OHIF 3.7.0 made DICOM SM a first-class modality, so a whole-slide image can be read in the
# browser rather than only fed to a training job. And the livelock's suspected root cause, a DQR
# thread leak, is fixed in DQR 3.0.0, which this stack now runs; the plugin itself also rewrote its
# DICOMweb backend in 3.7.0, so it is not the build FLIP#662 measured.
#
# The exclusion therefore becomes a switch rather than a verdict. It defaults off so existing
# deployments are unchanged, and the caveat worth knowing is unchanged too: the wedging was observed
# on bulk cohort imports of thousands of radiology studies, so an operator who pulls at that scale
# and sees imports stall should try turning this back off first.
XNAT_OHIF_VIEWER="${XNAT_OHIF_VIEWER:-false}"
case "${XNAT_OHIF_VIEWER,,}" in
  true|1|yes|on) ohif_enabled=true ;;
  *) ohif_enabled=false ;;
esac

required_prefixes=(
  "batch-launch-"
  "container-service-"
  "dicom-query-retrieve-"
)
if [[ "${ohif_enabled}" == "true" ]]; then
  required_prefixes+=("ohif-viewer-")
  echo "🔎 OHIF viewer enabled (XNAT_OHIF_VIEWER=${XNAT_OHIF_VIEWER})."
fi
expected_prefixes="$(printf '%s, ' "${required_prefixes[@]}")"
expected_prefixes="${expected_prefixes%, }"

# A jar that exists is not a jar that works. An interrupted sync, a full disk, or a half-finished
# hand copy leaves a zero-byte or truncated file that a filename check accepts. Development
# bind-mounts this directory straight into the running container, so such a file boots an XNAT with
# no plugin routes — and wait-for-xnat-plugins.sh then spends its whole readiness budget blaming the
# DQR plugin for what is really a local cache fault.
jar_is_valid() {
  local jar="$1"

  [[ -s "$jar" ]] || return 1

  if command -v unzip >/dev/null 2>&1; then
    # -l reads the central directory, which lives at the END of the archive, so truncation is
    # caught without paying the full CRC cost of -t on a large jar.
    unzip -l "$jar" >/dev/null 2>&1
  else
    # No unzip available: fall back to the zip local-file-header magic, which still rejects the
    # empty and plainly-not-an-archive cases.
    [[ "$(head -c 2 "$jar" 2>/dev/null)" == "PK" ]]
  fi
}

find_missing_prefixes() {
  local missing=()
  local prefix jar found

  for prefix in "${required_prefixes[@]}"; do
    found=""
    for jar in "${PLUGIN_DIR}/${prefix}"*.jar; do
      [[ -e "$jar" ]] || continue
      if jar_is_valid "$jar"; then
        found=1
      else
        # Removed rather than merely ignored: `aws s3 sync` compares size and mtime, so a corrupt
        # file of plausible size would survive the re-sync that this function is about to trigger.
        echo "⚠️  Discarding unreadable plugin jar: ${jar}" >&2
        rm -f "$jar"
      fi
    done
    [[ -n "$found" ]] || missing+=("${prefix}")
  done

  printf '%s\n' "${missing[@]:-}"
}

echo "📦 Ensuring required XNAT plugins are available..."
mkdir -p "${PLUGIN_DIR}"

missing_prefixes="$(find_missing_prefixes)"
synced_prefix="$(cat "${STAMP_FILE}" 2>/dev/null || true)"
if [[ -z "${missing_prefixes}" && "${synced_prefix}" == "${S3_PREFIX}" ]]; then
  echo "✅ Required plugins already exist locally (synced from ${S3_PREFIX}). Skipping S3 sync."
else
  if ! command -v aws >/dev/null 2>&1; then
    echo "❌ ERROR: Local plugins need an S3 sync and AWS CLI is not installed."
    if [[ -n "${missing_prefixes}" ]]; then
      echo "   Missing plugin families: ${missing_prefixes//$'\n'/ }"
    else
      echo "   Local jars were synced from '${synced_prefix:-<unknown>}' but this build needs '${S3_PREFIX}'."
      echo "   If the jars in ${PLUGIN_DIR} were hand-provisioned for this XNAT version, record that with:"
      echo "     echo '${S3_PREFIX}' > ${STAMP_FILE}"
    fi
    exit 1
  fi

  if [[ -n "${missing_prefixes}" ]]; then
    echo "⬇️ Missing plugin families locally: ${missing_prefixes//$'\n'/ }"
  else
    echo "🔁 Local plugins were synced from '${synced_prefix:-<unknown>}' but this build needs '${S3_PREFIX}'."
  fi
  echo "📦 Syncing plugins from S3..."
  # When the viewer is off, the trailing --exclude wins over --include for matching keys, so the jar
  # is neither downloaded nor (despite --delete) removed. See the required_prefixes note above.
  ohif_filter=(--exclude "ohif-viewer-*")
  if [[ "${ohif_enabled}" == "true" ]]; then
    ohif_filter=()
  fi
  aws s3 sync "s3://${S3_BUCKET}/${S3_PREFIX}/" "${PLUGIN_DIR}/" --delete \
    --exclude "*" --include "*.jar" "${ohif_filter[@]}"
  printf '%s\n' "${S3_PREFIX}" > "${STAMP_FILE}"
fi

if [[ "${ohif_enabled}" != "true" ]]; then
  # The sync's --exclude hides these keys from --delete, so a jar left by a previous run when the
  # viewer was enabled would survive switching it off. Remove it here so the switch works in both
  # directions rather than only latching on.
  for stale in "${PLUGIN_DIR}"/ohif-viewer-*.jar; do
    [[ -e "${stale}" ]] || continue
    echo "🧹 Removing $(basename "${stale}") (XNAT_OHIF_VIEWER is off)."
    rm -f "${stale}"
  done
fi

missing_prefixes="$(find_missing_prefixes)"
if [[ -n "${missing_prefixes}" ]]; then
  echo "❌ ERROR: Missing required plugin families after sync: ${missing_prefixes//$'\n'/ }"
  echo "   Expected plugin prefixes: ${expected_prefixes}"
  # The viewer is the one family that is opt-in, and it was added to the artifact bucket later than
  # the others -- so "enabled but absent from S3" is the likely first failure, and it deserves the
  # fix rather than a bare list of prefixes.
  if [[ "${ohif_enabled}" == "true" && "${missing_prefixes}" == *"ohif-viewer-"* ]]; then
    echo ""
    echo "   XNAT_OHIF_VIEWER is on, but no ohif-viewer jar is in"
    echo "   s3://${S3_BUCKET}/${S3_PREFIX}/. Either upload the plugin build matching this XNAT"
    echo "   version, or set XNAT_OHIF_VIEWER=false in trust/xnat/.env to build without the viewer."
  fi
  exit 1
fi

echo "✅ Required plugins are available."
