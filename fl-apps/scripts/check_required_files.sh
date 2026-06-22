#!/bin/bash
set -e
cd "$(dirname "$0")"

SRC_DIR="src"
AGG_FILE="$SRC_DIR/required_files.json"
FAIL=0

echo '{}' > "$AGG_FILE"

# Only consider first-level directories under src
for d in "$SRC_DIR"/*; do
  if [ -d "$d" ]; then
    dname=$(basename "$d")
    REQ_FILE="$d/required_files.json"
    if [ ! -f "$REQ_FILE" ]; then
      echo "Missing required_files.json in $dname" >&2
      FAIL=1
      continue
    fi
    # Validate structure: must be a JSON array
    if ! jq -e 'type == "array"' "$REQ_FILE" > /dev/null; then
      echo "Invalid structure in $REQ_FILE: must be a JSON array" >&2
      FAIL=1
      continue
    fi
    # Add to aggregate
    jq --arg d "$dname" --slurpfile arr "$REQ_FILE" '. + {($d): $arr[0]}' "$AGG_FILE" > "$AGG_FILE.tmp" && mv "$AGG_FILE.tmp" "$AGG_FILE"
  fi
done

if [ $FAIL -ne 0 ]; then
  echo "required_files.json check failed." >&2
  exit 1
fi

echo "Aggregated required_files.json:"
cat "$AGG_FILE"
