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
# Generate Flower TLS certificates + SuperNode credentials for one FL network,
# in-tree. Self-contained port of flip-fl-base-flower's `generate-tls-certificates`
# Makefile target: runs the vendored generate_creds.py (no `flwr new` scaffold) into
# deploy/providers/flower/certs/<net>/{certificates,keys} — the gitignored
# FL_PROVISIONED_DIR the Flower compose mounts.
#
# Requires: uv (pulls `cryptography` ephemerally via --with).
# Usage: generate-tls-certificates.sh net-1     (call once per net: net-1, net-2)
set -euo pipefail

NET="${1:?usage: generate-tls-certificates.sh <net-1|net-2>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(git -C "$HERE" rev-parse --show-toplevel)"
OUT="${FLOWER_CERTS_DIR:-$REPO_ROOT/deploy/providers/flower/certs}/$NET"

mkdir -p "$OUT"
echo "🔐 Generating Flower TLS certs + SuperNode key pairs for $NET → $OUT"
# generate_creds.py writes certificates/ + keys/ into CWD (and clears any existing),
# so run it inside the per-net output dir.
( cd "$OUT" && uv run --no-project --with cryptography "$HERE/generate_creds.py" )

# Tighten permissions. Private keys are owner+group readable only (640): the FL
# containers read them via the compose `group_add: ${DOCKER_GID}` (the host group),
# so they never need to be world-readable. The CA private key (ca.key) is only used
# to sign at generation time and is not read at runtime, so it stays owner-only (600).
# Public material (certs + .pub keys) is world-readable (644).
chmod 600 "$OUT"/certificates/ca.key
chmod 640 "$OUT"/certificates/server.key
# Public certs must be world-readable (644) so the container user can read them even
# under a restrictive operator umask (e.g. 077) — generate_creds.py writes them under
# the operator's umask. ca.crt verifies the server; server.pem is the SuperLink's cert
# chain. Both are mounted read-only into the FL containers.
chmod 644 "$OUT"/certificates/ca.crt "$OUT"/certificates/server.pem
chmod 644 "$OUT"/keys/*.pub
find "$OUT"/keys -type f ! -name '*.pub' -exec chmod 640 {} +
echo "✅ $NET ready: certificates=[$(ls "$OUT"/certificates | tr '\n' ' ')] keys=[$(ls "$OUT"/keys | tr '\n' ' ')]"
