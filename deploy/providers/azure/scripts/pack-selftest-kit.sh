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
# flip-node: the on-node helper for an Azure FLIP trust node (FLIP#1390). Installed by
#
# Pack the FL kit a hubless node self-test runs on (FLIP#1390), on the operator's machine:
# provision the backend's dev kit here, then tarball only what Trust_1's client needs, in the
# layout package-onprem-trust-kit uses (fl-kit/...). The node downloads it from the kit drop, so
# it never carries provisioning tooling, and no CA or server private key leaves this machine.
#
# Usage: pack-selftest-kit.sh nvflare|flower <out.tar.gz>

set -euo pipefail

BACKEND="${1:-}"
OUT="${2:-}"
case "${BACKEND}" in
    nvflare | flower) ;;
    *)
        echo "usage: pack-selftest-kit.sh nvflare|flower <out.tar.gz>" >&2
        exit 2
        ;;
esac
[ -n "${OUT}" ] || {
    echo "usage: pack-selftest-kit.sh nvflare|flower <out.tar.gz>" >&2
    exit 2
}

REPO_ROOT="${FLIP_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT

make -C "${REPO_ROOT}/fl-services/${BACKEND}" provision >&2

if [ "${BACKEND}" = nvflare ]; then
    slot="${REPO_ROOT}/fl-services/nvflare/provision/workspace-dev/net-1/services/Trust_1"
    mkdir -p "${STAGE}/fl-kit/net-1/services"
    cp -R "${slot}" "${STAGE}/fl-kit/net-1/services/Trust_1"
    # NVFLARE's client layout needs transfer/ even when empty (as package-onprem-trust-kit does).
    mkdir -p "${STAGE}/fl-kit/net-1/services/Trust_1/transfer"
else
    creds="${REPO_ROOT}/fl-services/flower/provision/creds/net-1"
    mkdir -p "${STAGE}/fl-kit/net-1/certificates" "${STAGE}/fl-kit/net-1/keys"
    cp "${creds}/certificates/ca.crt" "${STAGE}/fl-kit/net-1/certificates/ca.crt"
    cp "${creds}/keys/supernode_credentials_1" "${STAGE}/fl-kit/net-1/keys/supernode_credentials_1"
fi

mkdir -p "$(dirname "${OUT}")"
# COPYFILE_DISABLE: macOS tar would otherwise add ._* metadata files beside every member.
COPYFILE_DISABLE=1 tar -C "${STAGE}" -czf "${OUT}" fl-kit
echo "Packed ${OUT}"
