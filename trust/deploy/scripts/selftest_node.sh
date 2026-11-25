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
# Hubless self-test of this host's trust stack (FLIP#1390): generate a kit that needs no hub,
# bring the production trust stack up as slot 1, check it, write a report, tear it down.
# Run on the node (as root) through `make -C trust selftest-node FL_BACKEND=nvflare|flower`.

set -uo pipefail

BACKEND="${1:-}"
case "${BACKEND}" in
    nvflare | flower) ;;
    *)
        echo "usage: selftest_node.sh nvflare|flower" >&2
        exit 2
        ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT="${SELFTEST_OUT:-/opt/flip/selftest}"
DATA_ROOT="${SELFTEST_DATA_ROOT:-/opt/flip/data/trust-1}"
DOCKER_TAG="${SELFTEST_DOCKER_TAG:-stag}"
KIT_OUT="${SELFTEST_KIT_OUT:-${REPO_ROOT}/trust/.env.SELFTEST.prod}"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CHECKS_FILE="$(mktemp)"
STACK_STARTED=false
# Teardown lives in the EXIT trap, so an interrupt or an unbound variable under set -u cannot
# leave the self-test stack running on a node whose next step may be a join.
cleanup() {
    rm -f "${CHECKS_FILE}"
    if [ "${STACK_STARTED}" = true ] && [ "${SELFTEST_KEEP_UP:-0}" != 1 ]; then
        make -C "${REPO_ROOT}/trust" down-trust KIT=SELFTEST HUB=prod FL_BACKEND="${BACKEND}" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -p "${OUT}"

record() { # <name> <ok:true|false> <detail>
    printf '%s\t%s\t%s\n' "$1" "$2" "${3//$'\t'/ }" >> "${CHECKS_FILE}"
    if [ "$2" = true ]; then echo "  ✅ $1"; else echo "  ❌ $1: $3"; fi
}

if [ "${BACKEND}" = nvflare ]; then
    DEV_KIT_DIR="${SELFTEST_DEV_KIT_DIR:-${REPO_ROOT}/fl-services/nvflare/provision/workspace-dev}"
else
    DEV_KIT_DIR="${SELFTEST_DEV_KIT_DIR:-${REPO_ROOT}/fl-services/flower/provision/creds}"
fi
# Where the stack reads the FL kit from: azure.yml creates /opt/flip/fl-kit.
FL_KIT_DIR="${SELFTEST_FL_KIT_DIR:-/opt/flip/fl-kit}"
PREP_OK=true

echo "== Self-test (${BACKEND}) started ${STARTED}"
# A production node never provisions: it receives its slot's kit (SELFTEST_SKIP_PROVISION=1 with
# SELFTEST_DEV_KIT_DIR pointing at the delivered fl-kit/). Provisioning here is for hosts that
# carry the development tooling.
if [ "${SELFTEST_SKIP_PROVISION:-0}" = 1 ]; then
    if [ -d "${DEV_KIT_DIR}/net-1" ]; then
        record "fl kit on the node" true "${DEV_KIT_DIR}"
    else
        record "fl kit on the node" false "no FL kit at ${DEV_KIT_DIR}"
        PREP_OK=false
    fi
elif make -C "${REPO_ROOT}/fl-services/${BACKEND}" provision >/dev/null; then
    record "dev fl kit" true "${DEV_KIT_DIR}"
else
    record "dev fl kit" false "make -C fl-services/${BACKEND} provision failed"
    PREP_OK=false
fi

# The dev kit is provisioned by root, but neither FL client runs as root: the NVFLARE client
# is uid 1000 and writes into its slot's local/, and the Flower supernode is uid/gid 49999
# and must read its private key. Stage a copy the client can use, as the EC2 path does when
# it syncs the kit from S3.
stage_fl_kit() {
    rm -rf "${FL_KIT_DIR}/net-1"
    if [ "${BACKEND}" = nvflare ]; then
        mkdir -p "${FL_KIT_DIR}/net-1/services" &&
            cp -R "${DEV_KIT_DIR}/net-1/services/Trust_1" "${FL_KIT_DIR}/net-1/services/Trust_1" &&
            chown -R 1000:1000 "${FL_KIT_DIR}/net-1"
    else
        local key="${FL_KIT_DIR}/net-1/keys/supernode_credentials_1"
        mkdir -p "${FL_KIT_DIR}/net-1/certificates" "${FL_KIT_DIR}/net-1/keys" &&
            cp "${DEV_KIT_DIR}/net-1/certificates/ca.crt" "${FL_KIT_DIR}/net-1/certificates/ca.crt" &&
            chmod 0644 "${FL_KIT_DIR}/net-1/certificates/ca.crt" &&
            cp "${DEV_KIT_DIR}/net-1/keys/supernode_credentials_1" "${key}" &&
            chgrp 49999 "${key}" &&
            chmod 0640 "${key}"
    fi
}
if stage_fl_kit; then
    record "fl kit staged" true "${FL_KIT_DIR} (owned for the ${BACKEND} client)"
else
    record "fl kit staged" false "could not stage ${DEV_KIT_DIR} into ${FL_KIT_DIR}"
    PREP_OK=false
fi

# Never start the stack on the previous run's kit: remove it first, so a failed generation
# leaves no kit at all.
rm -f "${KIT_OUT}"
KIT_ARGS=(--backend "${BACKEND}" --fl-kit-dir "${FL_KIT_DIR}" --data-root "${DATA_ROOT}"
    --docker-tag "${DOCKER_TAG}" --out "${KIT_OUT}" --force)
if [ -n "${SELFTEST_KIT_TEMPLATE:-}" ]; then
    KIT_ARGS+=(--template "${SELFTEST_KIT_TEMPLATE}")
fi
# false: no sample data, for a node with no route to Hugging Face or PyPI.
if [ -n "${SELFTEST_TRUST_DATA_SEED:-}" ]; then
    KIT_ARGS+=(--trust-data-seed "${SELFTEST_TRUST_DATA_SEED}")
fi
if python3 "${REPO_ROOT}/trust/deploy/scripts/selftest_kit.py" "${KIT_ARGS[@]}" >/dev/null; then
    record "kit file" true "${KIT_OUT}"
else
    record "kit file" false "selftest_kit.py failed"
    PREP_OK=false
fi

if [ "${PREP_OK}" != true ]; then
    record "stack up" false "skipped: a preparation step above failed"
elif STACK_STARTED=true && make -C "${REPO_ROOT}/trust" up-trust KIT=SELFTEST HUB=prod FL_BACKEND="${BACKEND}"; then
    record "stack up" true "make -C trust up-trust KIT=SELFTEST HUB=prod"
else
    record "stack up" false "up-trust failed; see the output above"
fi

# The checks themselves are check_node.py's, the ones every trust shape runs (node_checks.py). It
# heads its report with the rows recorded above, and its exit code is this script's.
PREFIX="${FLIP_INSTANCE:+${FLIP_INSTANCE}-}"
CHECK_NODE=("${SELFTEST_CHECK_NODE:-python3}")
[ -n "${SELFTEST_CHECK_NODE:-}" ] || CHECK_NODE+=("${REPO_ROOT}/trust/deploy/scripts/check_node.py")
"${CHECK_NODE[@]}" --project "${PREFIX}trust1" --xnat-stack "${PREFIX}xnat1" --backend "${BACKEND}" \
    --kit-file "${KIT_OUT}" --out "${OUT}" --earlier-rows "${CHECKS_FILE}" \
    --stem "selftest-${BACKEND}-${STAMP}" --latest "latest-${BACKEND}" --title "Node self-test: ${BACKEND}" \
    --fl-settle-seconds "${SELFTEST_FL_SETTLE_SECONDS:-45}"
