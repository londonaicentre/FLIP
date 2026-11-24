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
address = "${SUPERLINK_ADDRESS:-superlink:8000}"
root-certificates = "${SUPERLINK_ROOT_CERTIFICATES:-/certs/ca.crt}"
EOF

echo "Configured Flower CLI for SuperLink Control API at ${SUPERLINK_ADDRESS:-superlink:8000}"

# The SuperLink serves the Control API over TLS with the kit's server.pem (mounted beside ca.crt),
# so a certificate that does not name the host we dial can never pass the readiness loop below.
# Check it first and name the fix, instead of timing out two minutes later (FLIP#1738).
superlink_host="${SUPERLINK_ADDRESS:-superlink:8000}"
superlink_host="${superlink_host%:*}"
server_cert="${SUPERLINK_SERVER_CERTIFICATE:-$(dirname "${SUPERLINK_ROOT_CERTIFICATES:-/certs/ca.crt}")/server.pem}"
if [ -r "$server_cert" ]; then
    python - "$server_cert" "$superlink_host" <<'PY' || exit 1
import sys

from cryptography import x509

cert_path, host = sys.argv[1], sys.argv[2]
with open(cert_path, "rb") as f:
    cert = x509.load_pem_x509_certificate(f.read())
san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
names = san.get_values_for_type(x509.DNSName) + [str(ip) for ip in san.get_values_for_type(x509.IPAddress)]
if host not in names:
    print(
        f"ERROR: {cert_path} does not name {host} (its SANs: {', '.join(names)}). Re-provision the kit with "
        f"FLOWER_EXTRA_SERVER_SANS including {host} (fl-services/flower/Makefile) and upload it under a new kit date.",
        file=sys.stderr,
    )
    sys.exit(1)
PY
fi

# Compose starts this one-shot on "superlink started", not "superlink ready", and the SuperLink's
# HTTP Control API (uvicorn, flwr>=1.37) takes a few seconds to accept connections. A register
# call made before that returns {"success": false} with exit 0, so the key silently goes
# unregistered and its SuperNode is refused at activation. Wait for the API to answer first.
for attempt in $(seq 1 "${SUPERLINK_READY_ATTEMPTS:-60}"); do
    if flwr supernode ls local --format json 2>/dev/null | grep -q '"success": true'; then
        break
    fi
    if [ "$attempt" -eq "${SUPERLINK_READY_ATTEMPTS:-60}" ]; then
        echo "ERROR: SuperLink Control API at ${SUPERLINK_ADDRESS:-superlink:8000} did not become ready" >&2
        exit 1
    fi
    sleep 2
done
echo "SuperLink Control API is ready."

# TRUST_NAMES names each key's node (one per /keys/*.pub, sorted). Unset, each key names its own:
# supernode_credentials_<N>.pub is Trust_<N>, the slot flip-api derives from the same file name
# (FLIP#1942), so the keys are the only list and one added later never renames another.
# FL_API_ADDRESS is optional and, when set, needs FL_API_KEY (every FL API route but /health
# requires it; FL_API_KEY_HEADER renames the header), and a mapping the FL API does not take fails the run.
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

# Find all public key files: in glob order when TRUST_NAMES pairs names with them by position,
# else re-sorted by number below.
keys_dir="${KEYS_DIR:-/keys}"
pub_keys=("$keys_dir"/*.pub)
if [ ! -e "${pub_keys[0]}" ]; then
    echo "ERROR: No .pub key files found in ${keys_dir}/" >&2
    exit 1
fi

if [ -z "$raw_trust_names" ]; then
    # By number, so that a key added later is registered after the ones already there:
    # the node ids of keys already registered are matched to them in that order (below).
    sorted_keys=$(printf '%s\n' "${pub_keys[@]}" | sort -V)
    pub_keys=()
    trust_names=()
    while IFS= read -r pub_key; do
        if [[ "$pub_key" =~ /supernode_credentials_([0-9]+)\.pub$ ]]; then
            pub_keys+=("$pub_key")
            trust_names+=("Trust_${BASH_REMATCH[1]}")
        else
            echo "ERROR: ${pub_key} is not supernode_credentials_<N>.pub, so it names no kit slot (or set TRUST_NAMES)" >&2
            exit 1
        fi
    done <<< "$sorted_keys"
fi

echo "Registering ${#pub_keys[@]} SuperNode key(s)..."

# The nodes this SuperLink already has, oldest first (`flwr supernode ls` sorts by registration
# time). A key it already holds is refused with "already in use" and no node id, and the listing
# names neither key nor node name, so those ids can only be matched to keys by position — which
# holds only when the keys already held are exactly the first ones here, one per listed node.
# Anything else (a node whose key is no longer on disk, say) and their mappings are left as they
# are rather than guessed.
existing_ids=()
if ls_output=$(flwr supernode ls local --format json 2>&1); then
    while IFS= read -r nid; do
        [ -n "$nid" ] && existing_ids+=("$nid")
    done <<< "$(echo "$ls_output" | grep -oE '"node-id": *"?[0-9]+' | grep -oE '[0-9]+$' || true)"
fi

# Collect node_id → trust_name mappings for FL API registration
declare -a node_ids=()
skipped=()

failed=0
idx=0
for pub_key in "${pub_keys[@]}"; do
    echo "  Registering ${pub_key}..."
    node_id=""

    # flwr>=1.38 stores --name against the registered key, and the SuperLink hands it to the
    # ServerApp through Grid.get_nodes() (NodeInfo.name) — the authenticated node → trust
    # identity FLIP#1270 filters on. Every key must therefore carry a trust name.
    if [ "$idx" -ge "${#trust_names[@]}" ] || [ -z "${trust_names[$idx]}" ]; then
        echo "ERROR: no trust name for ${pub_key} (key #$((idx + 1)), TRUST_NAMES has ${#trust_names[@]})" >&2
        exit 1
    fi
    if output=$(flwr supernode register "$pub_key" local --name "${trust_names[$idx]}" --format json 2>&1); then
        # Parse node_id from JSON output: {"success": true, "node-id": <id>}
        # node-id may be quoted ("123") or unquoted (123) depending on Flower version
        node_id=$(echo "$output" | grep -oE '"node-id": *"?[0-9]+' | grep -oE '[0-9]+$' || true)

        if [ -n "$node_id" ]; then
            # add_fl_kits_flower.sh releases a new slot only on this line, for its key.
            echo "    OK: ${pub_key##*/} node_id=${node_id}"
        elif echo "$output" | grep -q "Public key already in use"; then
            # Flower returns exit 0 with {"success": false} for already-registered keys
            echo "    SKIP (already registered): ${pub_key}"
            skipped+=("$idx")
        else
            # Exit 0 without a node id: {"success": false}, or output this script does not know.
            # Either way the key is not known to be registered, and its SuperNode may be refused.
            echo "    FAIL: ${output}" >&2
            failed=1
        fi
    elif echo "$output" | grep -q "Public key already in use"; then
        echo "    SKIP (already registered): ${pub_key}"
        skipped+=("$idx")
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

# Node ids for the keys already registered, by position (see existing_ids above).
if [ ${#skipped[@]} -gt 0 ]; then
    in_order=1
    for i in "${!skipped[@]}"; do
        [ "${skipped[$i]}" -eq "$i" ] || in_order=0
    done
    if [ "$in_order" -eq 1 ] && [ ${#skipped[@]} -eq ${#existing_ids[@]} ]; then
        for i in "${!skipped[@]}"; do
            node_ids[i]="${existing_ids[$i]}"
            echo "  Existing node_id=${existing_ids[$i]} is ${pub_keys[$i]##*/}"
        done
    else
        echo "  WARN: ${#skipped[@]} key(s) already registered, but the SuperLink lists ${#existing_ids[@]} node(s)" \
            "in an order that does not match them: their FL API mappings are left as they are."
    fi
fi

# Register node_id → trust_name mappings with FL API. They live only in its memory and only this
# one-shot writes them, so one it did not take fails the run: exit 0 left the trust shown as
# disconnected with its SuperNode online (FLIP#2047).
if [ -n "$fl_api_address" ] && [ ${#trust_names[@]} -gt 0 ]; then
    echo ""
    echo "Registering node → trust name mappings with FL API at ${fl_api_address}..."
    # Same start-vs-ready race as the SuperLink above: give the FL API a moment to come up.
    for attempt in $(seq 1 "${FL_API_READY_ATTEMPTS:-30}"); do
        if python -c "import urllib.request as u; u.urlopen('${fl_api_address}/openapi.json', timeout=3)" 2>/dev/null; then
            break
        fi
        if [ "$attempt" -eq "${FL_API_READY_ATTEMPTS:-30}" ]; then
            echo "ERROR: FL API at ${fl_api_address} did not become ready, so no node → trust name mapping was registered" >&2
            exit 1
        fi
        sleep 2
    done

    unmapped=()
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
req = urllib.request.Request('${fl_api_address}/register_node', data=data, headers={'Content-Type': 'application/json', '${FL_API_KEY_HEADER:-X-FL-API-Key}': '${FL_API_KEY:-}'}, method='POST')
urllib.request.urlopen(req, timeout=5)
" 2>&1); then
            echo "    OK: ${tname} → ${nid}"
        else
            echo "    FAIL: ${tname} was not registered with FL API: ${err}" >&2
            unmapped+=("$tname")
        fi
    done

    if [ ${#unmapped[@]} -gt 0 ]; then
        echo "ERROR: FL API at ${fl_api_address} did not take the mapping for: ${unmapped[*]} (check FL_API_KEY matches the FL API's)" >&2
        exit 1
    fi
    echo "Node registration complete."
else
    echo "FL_API_ADDRESS not set — skipping node → trust name registration."
fi
