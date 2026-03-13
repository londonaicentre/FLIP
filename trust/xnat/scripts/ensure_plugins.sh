#!/usr/bin/env bash

# Script to ensure required XNAT plugins are present in the local plugin directory.
# If any required plugin families are missing, it attempts to sync them from the specified S3 bucket.
# If they are already present, it skips the S3 sync command, so it does not rely on S3 access if the plugins are already available locally.
# Usage: ./ensure_plugins.sh <plugin_dir> <s3_bucket>

set -euo pipefail

PLUGIN_DIR="${1:-}"
S3_BUCKET="${2:-}"

if [[ -z "${PLUGIN_DIR}" || -z "${S3_BUCKET}" ]]; then
  echo "Usage: $0 <plugin_dir> <s3_bucket>"
  exit 1
fi

required_prefixes=(
  "batch-launch-"
  "container-service-"
  "dicom-query-retrieve-"
  "ohif-viewer-"
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
if [[ -z "${missing_prefixes}" ]]; then
  echo "✅ Required plugins already exist locally. Skipping S3 sync."
else
  if ! command -v aws >/dev/null 2>&1; then
    echo "❌ ERROR: Missing local plugins and AWS CLI is not installed."
    echo "   Missing plugin families: ${missing_prefixes//$'\n'/ }"
    exit 1
  fi

  echo "⬇️ Missing plugin families locally: ${missing_prefixes//$'\n'/ }"
  echo "📦 Syncing plugins from S3..."
  aws s3 sync "s3://${S3_BUCKET}/xnat/plugins/" "${PLUGIN_DIR}/" --delete --exclude "*" --include "*.jar"
fi

missing_prefixes="$(find_missing_prefixes)"
if [[ -n "${missing_prefixes}" ]]; then
  echo "❌ ERROR: Missing required plugin families after sync: ${missing_prefixes//$'\n'/ }"
  echo "   Expected plugin prefixes: ${expected_prefixes}"
  exit 1
fi

echo "✅ Required plugins are available."
