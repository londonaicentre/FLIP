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
# cloud-init at /usr/local/sbin/flip-node and driven over `az vm run-command`, so the node
# needs no inbound access. Long steps run as transient systemd units, so Run Command's
# output and time limits never cut them off: read them back with `flip-node logs <unit>`.

set -euo pipefail

NODE_ENV="${FLIP_NODE_ENV:-/etc/flip/node.env}"
FLIP_DIR="${FLIP_DIR:-/opt/flip}"
DISK_CANDIDATES="${FLIP_DISK_CANDIDATES:-/dev/disk/azure/data/by-lun/0 /dev/disk/azure/scsi1/lun0}"
DISK_WAIT_SECONDS="${FLIP_DISK_WAIT_SECONDS:-300}"
FSTAB="${FLIP_FSTAB:-/etc/fstab}"
REPO_DIR="${FLIP_DIR}/FLIP"
FLIP_NODE_BIN="${FLIP_NODE_BIN:-/usr/local/sbin/flip-node}"
IMDS_TOKEN_URL="${FLIP_IMDS_TOKEN_URL:-http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://storage.azure.com/}"
FETCH_ATTEMPTS="${FLIP_FETCH_ATTEMPTS:-10}"
FETCH_BACKOFF_SECONDS="${FLIP_FETCH_BACKOFF_SECONDS:-30}"

# shellcheck disable=SC1090
source "${NODE_ENV}"

usage() {
    echo "usage: flip-node provision [REF] | reprovision [REF] | status | fetch-kit NAME.tar.gz | selftest nvflare|flower | logs UNIT [LINES] | report nvflare|flower" >&2
    exit 2
}

# Terraform attaches the data disk as its own resource, so it can appear after first boot
# starts. Wait for it; never fall back to the OS disk.
find_data_disk() {
    local waited=0 dev
    while [ "${waited}" -le "${DISK_WAIT_SECONDS}" ]; do
        for dev in ${DISK_CANDIDATES}; do
            if [ -e "${dev}" ]; then
                echo "${dev}"
                return 0
            fi
        done
        sleep 1
        waited=$((waited + 1))
    done
    echo "ERROR: the data disk did not appear within ${DISK_WAIT_SECONDS}s (looked for: ${DISK_CANDIDATES})." >&2
    echo "       Refusing to provision onto the OS disk. Check the disk attachment, then: flip-node reprovision" >&2
    return 1
}

mount_data_disk() {
    local dev
    dev="$(find_data_disk)"
    if ! blkid "${dev}" >/dev/null 2>&1; then
        echo "Formatting blank data disk ${dev}"
        mkfs.ext4 -L flipdata "${dev}"
    fi
    mkdir -p "${FLIP_DIR}"
    if ! grep -q "LABEL=flipdata" "${FSTAB}" 2>/dev/null; then
        echo "LABEL=flipdata ${FLIP_DIR} ext4 defaults,nofail 0 2" >> "${FSTAB}"
    fi
    if ! mountpoint -q "${FLIP_DIR}"; then
        mount "${FLIP_DIR}"
    fi
}

checkout() {
    local ref="${1:-${FLIP_REF}}"
    if [ ! -d "${REPO_DIR}/.git" ]; then
        git clone --quiet "${FLIP_REPO_URL}" "${REPO_DIR}"
    fi
    git -C "${REPO_DIR}" fetch --quiet --tags origin
    git -C "${REPO_DIR}" checkout --quiet --detach "${ref}"
}

provision() {
    mount_data_disk
    checkout "${1:-}"
    # cloud-init wrote flip-node once, at first boot; the checkout's copy is the one for this ref.
    local shipped="${REPO_DIR}/deploy/providers/azure/vm/templates/flip-node.sh"
    if [ -f "${shipped}" ]; then
        install -m 0755 "${shipped}" "${FLIP_NODE_BIN}"
    fi
    ansible-galaxy install -r "${REPO_DIR}/trust/deploy/ansible/requirements.yml"
    ansible-playbook -i localhost, -c local "${REPO_DIR}/trust/deploy/ansible/azure.yml" \
        -e "flip_owner=${FLIP_ADMIN_USER}" -e "fl_backend=${FL_BACKEND}"
    date -u +%Y-%m-%dT%H:%M:%SZ > "${FLIP_DIR}/.provisioned"
    echo "Provisioned at ref $(git -C "${REPO_DIR}" rev-parse HEAD)"
}

# Download a kit from the kit drop with the node's managed identity, refuse any member that
# could land outside the kit dir, extract it owner-only under ${FLIP_DIR}/kits/<name>/ and delete
# the tarball. The identity's read role can take minutes to propagate after an apply, so a
# refusal is retried before giving up.
fetch_kit() {
    local name="$1" token status attempt=1 kits dest tarball
    if ! [[ "${name}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*\.tar\.gz$ ]]; then
        echo "ERROR: fetch-kit takes a blob name like flip-trust-kit-Trust_3.tar.gz (got '${name}')" >&2
        exit 2
    fi
    if [ -z "${KIT_DROP_URL:-}" ]; then
        echo "ERROR: this node has no kit drop (KIT_DROP_URL is empty in ${NODE_ENV})." >&2
        exit 1
    fi
    kits="${FLIP_DIR}/kits"
    dest="${kits}/${name%.tar.gz}"
    tarball="${kits}/${name}"
    mkdir -p "${kits}"
    chmod 700 "${kits}"
    while :; do
        token="$(curl -fsS --max-time 10 -H Metadata:true "${IMDS_TOKEN_URL}" |
            python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')"
        status="$(curl -sS --max-time 300 -o "${tarball}" -w '%{http_code}' \
            -H "Authorization: Bearer ${token}" -H "x-ms-version: 2021-08-06" \
            "${KIT_DROP_URL}/${name}" || echo 000)"
        [ "${status}" = 200 ] && break
        rm -f "${tarball}"
        if [ "${attempt}" -ge "${FETCH_ATTEMPTS}" ]; then
            echo "ERROR: could not fetch ${name} from the kit drop (HTTP ${status} after ${attempt} attempts)." >&2
            echo "  403: the node's read role may still be propagating (minutes after an apply), or the" >&2
            echo "       storage firewall does not admit the node subnet. 404: the blob was never uploaded," >&2
            echo "       or has expired (kits are deleted a day after upload): run make kit-upload again." >&2
            exit 1
        fi
        echo "Kit drop answered HTTP ${status}; retrying (${attempt}/${FETCH_ATTEMPTS})..."
        attempt=$((attempt + 1))
        sleep "${FETCH_BACKOFF_SECONDS}"
    done
    # An absolute path or a .. component could write outside the kit dir: refuse the whole kit.
    if tar -tzf "${tarball}" | grep -qE '(^/|(^|/)\.\.(/|$))'; then
        rm -f "${tarball}"
        echo "ERROR: ${name} has an unsafe member (absolute path or ..); nothing was extracted." >&2
        exit 1
    fi
    rm -rf "${dest}"
    mkdir -p "${dest}"
    chmod 700 "${dest}"
    tar -xzf "${tarball}" -C "${dest}" --no-same-owner
    rm -f "${tarball}"
    echo "Kit ${name} extracted to ${dest}"
}

# The self-test runs on a kit the operator packed and delivered (make selftest-kit), as a real
# trust runs on the kit its hub admin sends: the node carries no provisioning tooling.
run_selftest() {
    local backend="$1"
    if ! (fetch_kit "selftest-${backend}.tar.gz"); then
        echo "ERROR: no ${backend} self-test kit could be fetched. From the operator's machine:" >&2
        echo "       make -C deploy/providers/azure selftest-kit FL_BACKEND=${backend}" >&2
        exit 1
    fi
    SELFTEST_SKIP_PROVISION=1 SELFTEST_DEV_KIT_DIR="${FLIP_DIR}/kits/selftest-${backend}/fl-kit" \
        make -C "${REPO_DIR}/trust" selftest-node "FL_BACKEND=${backend}"
}

start_unit() {
    local unit="$1"
    shift
    systemd-run --unit="${unit}" --collect --property=TimeoutStartSec=infinity -- "$@"
    echo "Started ${unit}. Follow it with: flip-node logs ${unit}"
}

check_backend() {
    case "${1:-}" in
        nvflare | flower) ;;
        *)
            echo "ERROR: backend must be nvflare or flower (got '${1:-}')" >&2
            exit 2
            ;;
    esac
}

cmd="${1:-}"
if [ -z "${cmd}" ]; then
    usage
fi
shift
case "${cmd}" in
    provision) provision "${1:-}" ;;
    reprovision) start_unit flip-reprovision "${FLIP_NODE_BIN}" provision "${1:-}" ;;
    status)
        cloud-init status --long || true
        if [ -f "${FLIP_DIR}/.provisioned" ]; then echo "provisioned: $(cat "${FLIP_DIR}/.provisioned")"; else echo "provisioned: no"; fi
        systemctl list-units 'flip-*' --all --no-pager || true
        ;;
    fetch-kit) fetch_kit "${1:-}" ;;
    run-selftest)
        check_backend "${1:-}"
        run_selftest "$1"
        ;;
    selftest)
        check_backend "${1:-}"
        start_unit "flip-selftest-$1" "${FLIP_NODE_BIN}" run-selftest "$1"
        ;;
    logs)
        [ -n "${1:-}" ] || usage
        journalctl -u "$1" -n "${2:-80}" --no-pager
        ;;
    report)
        check_backend "${1:-}"
        cat "${FLIP_DIR}/selftest/latest-$1.md"
        ;;
    *) usage ;;
esac
