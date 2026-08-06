#!/usr/bin/env bash

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

# NOTE: ohif-viewer is intentionally NOT installed. FLIP uses XNAT purely as a
# DICOM store for FL training and never opens the OHIF viewer, but the plugin's
# per-session metadata-rebuild event listener is the dominant load on XNAT's
# Reactor EventBus and materially drives the back-pressure livelock that wedges
# bulk cohort imports (FLIP#662). It is excluded from the S3 sync below.
required_prefixes=(
  "batch-launch-"
  "container-service-"
  "dicom-query-retrieve-"
)
expected_prefixes="$(printf '%s, ' "${required_prefixes[@]}")"
expected_prefixes="${expected_prefixes%, }"

find_missing_prefixes() {
  local missing=()
  local prefix

  for prefix in "${required_prefixes[@]}"; do
    if ! ls "${PLUGIN_DIR}/${prefix}"*.jar >/dev/null 2>&1; then
      missing+=("${prefix}")
    fi
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
  # Exclude ohif-viewer: the trailing --exclude wins over --include for matching
  # keys, so it is neither downloaded nor (with --delete) kept locally. See the
  # required_prefixes note above (FLIP#662).
  aws s3 sync "s3://${S3_BUCKET}/${S3_PREFIX}/" "${PLUGIN_DIR}/" --delete \
    --exclude "*" --include "*.jar" --exclude "ohif-viewer-*"
  printf '%s\n' "${S3_PREFIX}" > "${STAMP_FILE}"
fi

missing_prefixes="$(find_missing_prefixes)"
if [[ -n "${missing_prefixes}" ]]; then
  echo "❌ ERROR: Missing required plugin families after sync: ${missing_prefixes//$'\n'/ }"
  echo "   Expected plugin prefixes: ${expected_prefixes}"
  exit 1
fi

echo "✅ Required plugins are available."
