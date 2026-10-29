#!/bin/sh
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
# Passes only when a Fleet request reaches the SuperLink through this proxy (FLIP#1517).
# The SuperLink refuses an unsigned, empty get-run with 401 (flwr's NodeAuthMiddleware).
# Every other status is the proxy answering by itself, so it fails: 404 off the allowlist
# (a mis-rendered map), 503 when it cannot reach or verify the SuperLink.
# `localhost` is a SAN of the provisioned server certificate.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
    --cacert "${FLEET_PROXY_CERTS_DIR}/ca.crt" \
    -X POST -H 'Content-Type: application/protobuf' --data-binary '' \
    "https://localhost:${FLEET_PROXY_LISTEN_PORT}/v1/fleet/get-run")
case "$code" in
    401) exit 0 ;;
    *) echo "fleet proxy -> SuperLink: HTTP ${code}" >&2; exit 1 ;;
esac
