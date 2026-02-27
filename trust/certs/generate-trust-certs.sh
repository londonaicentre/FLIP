#!/usr/bin/env bash
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
# generate-trust-certs.sh
#
# Generates a local CA and a TLS server certificate for the Trust service.
#
# Usage:
#   TRUST_HOST=<ip-or-hostname> bash trust/certs/generate-trust-certs.sh
#
# Required environment variable:
#   TRUST_HOST  — IP address or hostname that the Central Hub will connect to
#                 (used as a Subject Alternative Name in the server cert).
#
# Output files written to the same directory as this script (trust/certs/):
#   trust-ca.key        — CA private key  (git-ignored)
#   trust-ca.crt        — CA certificate  (distribute to Central Hub)
#   trust-server.key    — Server private key  (git-ignored)
#   trust-server.crt    — Server certificate signed by trust-ca  (git-ignored)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${SCRIPT_DIR}"

# ---------------------------------------------------------------------------
# Validate inputs
# ---------------------------------------------------------------------------
if [[ -z "${TRUST_HOST:-}" ]]; then
    echo "ERROR: TRUST_HOST environment variable is required." >&2
    echo "  Example: TRUST_HOST=192.168.1.100 bash $0" >&2
    echo "  Example: TRUST_HOST=trust.example.com bash $0" >&2
    exit 1
fi

# Detect whether TRUST_HOST looks like an IP address or a DNS name
if [[ "${TRUST_HOST}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    SAN="IP:${TRUST_HOST}"
else
    SAN="DNS:${TRUST_HOST}"
fi

CERT_DAYS="${CERT_DAYS:-825}"  # Apple/Chrome cap; override if needed

echo "Generating Trust TLS certificates..."
echo "  TRUST_HOST : ${TRUST_HOST}"
echo "  SAN        : ${SAN}"
echo "  Validity   : ${CERT_DAYS} days"
echo "  Output dir : ${OUT_DIR}"
echo ""

# ---------------------------------------------------------------------------
# Step 1 — Create local CA
# ---------------------------------------------------------------------------
echo "[1/4] Generating CA private key..."
openssl genrsa -out "${OUT_DIR}/trust-ca.key" 4096

echo "[2/4] Self-signing CA certificate..."
openssl req -x509 -new -nodes \
    -key "${OUT_DIR}/trust-ca.key" \
    -sha256 \
    -days "${CERT_DAYS}" \
    -out "${OUT_DIR}/trust-ca.crt" \
    -subj "/CN=Trust Local CA/O=FLIP Trust/OU=Local CA"

# ---------------------------------------------------------------------------
# Step 2 — Create server key
# ---------------------------------------------------------------------------
echo "[3/4] Generating server private key..."
openssl genrsa -out "${OUT_DIR}/trust-server.key" 2048

# ---------------------------------------------------------------------------
# Step 3 — Create CSR with SANs, then sign with the local CA
# ---------------------------------------------------------------------------
echo "[4/4] Creating CSR and signing server certificate..."

# Write a temporary OpenSSL config for the SAN extension
TMP_CNF="$(mktemp /tmp/trust-openssl-XXXXXX.cnf)"
trap 'rm -f "${TMP_CNF}"' EXIT

cat > "${TMP_CNF}" <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions     = v3_req
prompt             = no

[req_distinguished_name]
CN = ${TRUST_HOST}
O  = FLIP Trust
OU = Trust Server

[v3_req]
subjectAltName = ${SAN}
keyUsage       = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[v3_ca]
subjectAltName = ${SAN}
keyUsage       = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
EOF

# Generate CSR
openssl req -new \
    -key "${OUT_DIR}/trust-server.key" \
    -out "${OUT_DIR}/trust-server.csr" \
    -config "${TMP_CNF}"

# Sign with the local CA
openssl x509 -req \
    -in  "${OUT_DIR}/trust-server.csr" \
    -CA  "${OUT_DIR}/trust-ca.crt" \
    -CAkey "${OUT_DIR}/trust-ca.key" \
    -CAcreateserial \
    -out "${OUT_DIR}/trust-server.crt" \
    -days "${CERT_DAYS}" \
    -sha256 \
    -extfile "${TMP_CNF}" \
    -extensions v3_ca

# Remove the intermediate CSR (not needed after signing)
rm -f "${OUT_DIR}/trust-server.csr"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Done. Files written to ${OUT_DIR}:"
echo "  trust-ca.key      — CA private key (keep secret)"
echo "  trust-ca.crt      — CA certificate (copy to Central Hub flip-api container)"
echo "  trust-server.key  — Server private key (keep secret, mount into nginx)"
echo "  trust-server.crt  — Server certificate (mount into nginx)"
echo ""
echo "Verify with:"
echo "  openssl verify -CAfile ${OUT_DIR}/trust-ca.crt ${OUT_DIR}/trust-server.crt"
