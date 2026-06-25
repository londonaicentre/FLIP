#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

SRC_DIR="."
FAIL=0

# Templates live under per-backend subdirs (fl-apps/<backend>/<template>/). For every backend
# present (nvflare today; flower lands via #622, picked up automatically) we regenerate that
# backend's aggregate manifest fl-apps/<backend>/required_files.json from its per-template arrays,
# keyed by template name. That committed manifest is the exact object synced to
# s3://<bucket>/base-application/<backend>/required_files.json and consumed by flip-api — so the
# per-template required_files.json arrays are the single source of truth. CI regenerates and runs
# `git diff --exit-code` to fail on drift; there is deliberately no combined cross-backend file
# (each backend owns its own keyspace, so template names never collide between backends).
for backend_dir in "$SRC_DIR"/*/; do
  bname=$(basename "$backend_dir")
  # Skip non-backend dirs (none today; future-proofing against helper/output dirs).
  case "$bname" in runs | scripts | tutorials) continue ;; esac

  AGG_FILE="${backend_dir}required_files.json"
  AGG='{}'

  for d in "$backend_dir"*/; do
    [ -d "$d" ] || continue
    dname=$(basename "$d")
    REQ_FILE="$d/required_files.json"
    if [ ! -f "$REQ_FILE" ]; then
      echo "Missing required_files.json in $bname/$dname" >&2
      FAIL=1
      continue
    fi
    # Validate structure: must be a JSON array
    if ! jq -e 'type == "array"' "$REQ_FILE" > /dev/null; then
      echo "Invalid structure in $REQ_FILE: must be a JSON array" >&2
      FAIL=1
      continue
    fi
    AGG=$(jq --arg d "$dname" --slurpfile arr "$REQ_FILE" '. + {($d): $arr[0]}' <<< "$AGG")
  done

  if [ "$AGG" = '{}' ]; then
    echo "No templates with required_files.json found under $bname/ — refusing to write an empty manifest" >&2
    FAIL=1
    continue
  fi

  # Sorted keys (-S) for byte-stable output the CI drift-guard can diff; array element order is
  # preserved, so non-uniform templates (e.g. evaluation) keep their own file list.
  jq -S -n --argjson agg "$AGG" '$agg' > "$AGG_FILE"
  echo "Wrote $AGG_FILE:"
  cat "$AGG_FILE"
done

if [ $FAIL -ne 0 ]; then
  echo "required_files.json check failed. Fix the issues above, re-run 'bash fl-apps/check_required_files.sh', and commit the regenerated fl-apps/<backend>/required_files.json." >&2
  exit 1
fi
