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

#!/usr/bin/env bash
set -euo pipefail

# Write Flower CLI config for TLS connection to SuperLink Control API
mkdir -p ~/.flwr
cat > ~/.flwr/config.toml <<EOF
[superlink.local]
address = "${SUPERLINK_ADDRESS:-superlink:9093}"
root-certificates = "${SUPERLINK_ROOT_CERTIFICATES:-/certs/ca.crt}"
EOF

echo "Configured Flower CLI for SuperLink Control API at ${SUPERLINK_ADDRESS:-superlink:9093}"

# Optional: TRUST_NAMES and FL_API_ADDRESS for node → trust name registration.
# TRUST_NAMES accepts both JSON list (["Trust_1", "Trust_2"]) and comma-separated (Trust_1,Trust_2).
raw_trust_names="${TRUST_NAMES:-}"
# Strip JSON brackets and quotes, then split on commas
raw_trust_names="${raw_trust_names#[}"
raw_trust_names="${raw_trust_names%]}"
raw_trust_names="${raw_trust_names//\"/}"
IFS=',' read -ra trust_names <<< "$raw_trust_names"
# Trim leading/trailing whitespace from each name
for i in "${!trust_names[@]}"; do
    trust_names[$i]=$(echo "${trust_names[$i]}" | xargs)
done
fl_api_address="${FL_API_ADDRESS:-}"

# Find all public key files (sorted alphabetically to match TRUST_NAMES order)
pub_keys=(/keys/*.pub)
if [ ${#pub_keys[@]} -eq 0 ]; then
    echo "ERROR: No .pub key files found in /keys/" >&2
    exit 1
fi

echo "Registering ${#pub_keys[@]} SuperNode key(s)..."

# Collect node_id → trust_name mappings for FL API registration
declare -a node_ids=()

failed=0
idx=0
for pub_key in "${pub_keys[@]}"; do
    echo "  Registering ${pub_key}..."
    node_id=""

    if output=$(flwr supernode register "$pub_key" local --format json 2>&1); then
        # Parse node_id from JSON output: {"success": true, "node-id": <id>}
        # node-id may be quoted ("123") or unquoted (123) depending on Flower version
        node_id=$(echo "$output" | grep -oP '"node-id":\s*"?\K[0-9]+' || true)

        if [ -n "$node_id" ]; then
            echo "    OK: node_id=${node_id}"
        elif echo "$output" | grep -qi "already in use"; then
            # Flower returns exit 0 with {"success": false} for already-registered keys
            echo "    SKIP (already registered): ${pub_key}"
            if ls_output=$(flwr supernode ls local --format json 2>&1); then
                all_ids=$(echo "$ls_output" | grep -oP '"node-id":\s*"?\K[0-9]+' || true)
                node_id=$(echo "$all_ids" | sed -n "$((idx + 1))p" || true)
                if [ -n "$node_id" ]; then
                    echo "    Discovered existing node_id=${node_id}"
                fi
            fi
        else
            echo "    WARN: Registration succeeded but could not parse node_id"
        fi
    elif echo "$output" | grep -qi "already in use"; then
        echo "    SKIP (already registered): ${pub_key}"
        if ls_output=$(flwr supernode ls local --format json 2>&1); then
            all_ids=$(echo "$ls_output" | grep -oP '"node-id":\s*"?\K[0-9]+' || true)
            node_id=$(echo "$all_ids" | sed -n "$((idx + 1))p" || true)
            if [ -n "$node_id" ]; then
                echo "    Discovered existing node_id=${node_id}"
            fi
        fi
    else
        echo "    FAIL: ${output}" >&2
        failed=1
    fi

    node_ids+=("$node_id")
    idx=$((idx + 1))
done

if [ "$failed" -ne 0 ]; then
    echo "ERROR: One or more registrations failed" >&2
    exit 1
fi

echo "All SuperNode keys registered successfully."

# Register node_id → trust_name mappings with FL API (best-effort)
if [ -n "$fl_api_address" ] && [ ${#trust_names[@]} -gt 0 ]; then
    echo ""
    echo "Registering node → trust name mappings with FL API at ${fl_api_address}..."

    for i in "${!node_ids[@]}"; do
        nid="${node_ids[$i]}"
        if [ -z "$nid" ]; then
            echo "  SKIP: No node_id for index ${i}"
            continue
        fi
        if [ "$i" -ge "${#trust_names[@]}" ]; then
            echo "  SKIP: No trust name for index ${i} (only ${#trust_names[@]} names configured)"
            continue
        fi
        tname="${trust_names[$i]}"
        echo "  Registering node_id=${nid} as '${tname}'..."
        if err=$(python -c "
import urllib.request, json
data = json.dumps({'name': '${tname}', 'node_id': '${nid}'}).encode()
req = urllib.request.Request('${fl_api_address}/register_node', data=data, headers={'Content-Type': 'application/json'}, method='POST')
urllib.request.urlopen(req, timeout=5)
" 2>&1); then
            echo "    OK: ${tname} → ${nid}"
        else
            echo "    WARN: Failed to register ${tname} with FL API: ${err}"
        fi
    done

    echo "Node registration complete."
else
    if [ -z "$fl_api_address" ]; then
        echo "FL_API_ADDRESS not set — skipping node → trust name registration."
    fi
    if [ ${#trust_names[@]} -eq 0 ]; then
        echo "TRUST_NAMES not set — skipping node → trust name registration."
    fi
fi
