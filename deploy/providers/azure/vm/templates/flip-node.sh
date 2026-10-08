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

# shellcheck disable=SC1090
source "${NODE_ENV}"

usage() {
    echo "usage: flip-node provision [REF] | reprovision [REF] | status | selftest nvflare|flower | logs UNIT [LINES] | report nvflare|flower" >&2
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
    ansible-galaxy install -r "${REPO_DIR}/trust/deploy/ansible/requirements.yml"
    ansible-playbook -i localhost, -c local "${REPO_DIR}/trust/deploy/ansible/azure.yml" \
        -e "flip_owner=${FLIP_ADMIN_USER}" -e "fl_backend=${FL_BACKEND}"
    date -u +%Y-%m-%dT%H:%M:%SZ > "${FLIP_DIR}/.provisioned"
    echo "Provisioned at ref $(git -C "${REPO_DIR}" rev-parse HEAD)"
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
[ -n "${cmd}" ] && shift || usage
case "${cmd}" in
    provision) provision "${1:-}" ;;
    reprovision) start_unit flip-reprovision /usr/local/sbin/flip-node provision "${1:-}" ;;
    status)
        cloud-init status --long || true
        if [ -f "${FLIP_DIR}/.provisioned" ]; then echo "provisioned: $(cat "${FLIP_DIR}/.provisioned")"; else echo "provisioned: no"; fi
        systemctl list-units 'flip-*' --all --no-pager || true
        ;;
    selftest)
        check_backend "${1:-}"
        start_unit "flip-selftest-$1" make -C "${REPO_DIR}/trust" selftest-node "FL_BACKEND=$1"
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
