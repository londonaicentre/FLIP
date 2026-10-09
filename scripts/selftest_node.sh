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

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${SELFTEST_OUT:-/opt/flip/selftest}"
DATA_ROOT="${SELFTEST_DATA_ROOT:-/opt/flip/data/trust-1}"
DOCKER_TAG="${SELFTEST_DOCKER_TAG:-stag}"
KIT_OUT="${SELFTEST_KIT_OUT:-${REPO_ROOT}/trust/.env.SELFTEST.production}"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CHECKS_FILE="$(mktemp)"
trap 'rm -f "${CHECKS_FILE}"' EXIT
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
if [ "${SELFTEST_SKIP_PROVISION:-0}" != 1 ]; then
    if make -C "${REPO_ROOT}/fl-services/${BACKEND}" provision >/dev/null; then
        record "dev fl kit" true "${DEV_KIT_DIR}"
    else
        record "dev fl kit" false "make -C fl-services/${BACKEND} provision failed"
        PREP_OK=false
    fi
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
if python3 "${REPO_ROOT}/scripts/selftest_kit.py" "${KIT_ARGS[@]}" >/dev/null; then
    record "kit file" true "${KIT_OUT}"
else
    record "kit file" false "selftest_kit.py failed"
    PREP_OK=false
fi

if [ "${PREP_OK}" != true ]; then
    record "stack up" false "skipped: a preparation step above failed"
elif make -C "${REPO_ROOT}/trust" up-trust KIT=SELFTEST PROD=true FL_BACKEND="${BACKEND}"; then
    record "stack up" true "make -C trust up-trust KIT=SELFTEST PROD=true"
else
    record "stack up" false "up-trust failed; see the output above"
fi

health() { # <name> <port>
    local body
    if body="$(curl -fsS --max-time 10 "http://127.0.0.1:$2/health" 2>&1)" && [[ "${body}" == *'"status":"ok"'* ]]; then
        record "$1 health" true "200 from :$2/health"
    else
        record "$1 health" false "no healthy answer from :$2/health (${body:0:120})"
    fi
}
health trust-api 8020
health imaging-api 8001
health data-access-api 8010

marker() { # <name> <path>
    if [ -s "$2" ] && grep -q '^version=' "$2"; then
        record "$1 seeded" true "$2"
    else
        record "$1 seeded" false "missing or empty seed marker $2"
    fi
}
marker omop "${DATA_ROOT}/omop/.seeded"
marker orthanc "${DATA_ROOT}/.orthanc-storage.seeded"

code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:8042/" 2>/dev/null || true)"
if [ "${code}" = 401 ]; then
    record "orthanc requires auth" true "401 without credentials"
else
    record "orthanc requires auth" false "expected 401 from :8042/, got ${code:-no answer}"
fi

# A real C-STORE from Orthanc to XNAT's receiver (the prod compose configures the XNAT
# modality), then XNAT's dicom.log must have grown with no importer error: the same test as
# the Helm chart's smoke-cstore, because a C-ECHO would pass while every store aborts.
ORTHANC_AUTH="$(grep -E '^ORTHANC_USERNAME=' "${KIT_OUT}" 2>/dev/null | cut -d= -f2-):$(grep -E '^ORTHANC_PASSWORD=' "${KIT_OUT}" 2>/dev/null | cut -d= -f2-)"
XNAT_CTR="$(docker ps --filter status=running --format '{{.Names}}' | grep -E 'xnat-web' | head -1)"
DICOM_LOG=/data/xnat/home/logs/dicom.log
# Only digits survive: the count comes from inside a container and is later used in shell
# arithmetic, which would otherwise evaluate (and run) whatever the container printed.
dicom_log_lines() {
    local count
    count="$(docker exec "${XNAT_CTR}" sh -c "wc -l < ${DICOM_LOG} 2>/dev/null || echo 0" 2>/dev/null | tr -dc '0-9')"
    echo "${count:-0}"
}
before="$(dicom_log_lines)"
instance="$(curl -fsS -u "${ORTHANC_AUTH}" "http://127.0.0.1:8042/instances?since=0&limit=1" 2>/dev/null | tr -d '[]" \n')"
store="$(curl -fsS -u "${ORTHANC_AUTH}" -X POST "http://127.0.0.1:8042/modalities/XNAT/store" \
    -d "{\"Resources\":[\"${instance}\"],\"Synchronous\":true}" 2>/dev/null || true)"
# XNAT logs the import after the store has returned, so poll for the log to grow rather than read
# it once (the Helm smoke sleeps a fixed SETTLE_SECONDS for the same reason).
after="$(dicom_log_lines)"
for ((waited = 0; waited < ${SELFTEST_CSTORE_SETTLE_SECONDS:-30} && after <= before; waited++)); do
    sleep 1
    after="$(dicom_log_lines)"
done
errors="$(docker exec "${XNAT_CTR}" sh -c "tail -n +$((before + 1)) ${DICOM_LOG} 2>/dev/null" 2>/dev/null |
    grep -cE 'AbstractMethodError|NoSuchMethodError|unable to read DICOM object null' || true)"
if [ -z "${XNAT_CTR}" ]; then
    record "xnat c-store" false "no running xnat-web container"
elif [ -z "${instance}" ]; then
    record "xnat c-store" false "Orthanc has no instance to send (did seeding run?)"
elif [[ "${store}" != *'"FailedInstancesCount" : 0'* && "${store}" != *'"FailedInstancesCount":0'* ]]; then
    record "xnat c-store" false "Orthanc's store to XNAT did not succeed: ${store:0:160}"
elif [ "${after}" -le "${before}" ] || [ "${errors:-0}" -gt 0 ]; then
    record "xnat c-store" false "XNAT dicom.log went ${before}->${after} lines with ${errors:-0} importer errors"
else
    record "xnat c-store" true "1 instance stored; dicom.log grew ${before}->${after} with no importer error"
fi

# A container on `restart: unless-stopped` is briefly "running" between crashes, so one look
# proves nothing: sample twice, a settle period apart, and require it running, not
# restarting, with an unchanged restart count.
fl_state() { docker inspect -f '{{.State.Running}} {{.State.Restarting}} {{.RestartCount}}' "$1" 2>/dev/null; }
FL_CTR="$(docker ps -a --format '{{.Names}}' | grep -E 'fl-client|supernode' | head -1)"
if [ -z "${FL_CTR}" ]; then
    record "fl client running" false "no fl-client or supernode container"
else
    first="$(fl_state "${FL_CTR}")"
    sleep "${SELFTEST_FL_SETTLE_SECONDS:-45}"
    second="$(fl_state "${FL_CTR}")"
    if [[ "${first}" == "true false "* && "${second}" == "${first}" ]]; then
        record "fl client running" true "${FL_CTR} steady (no FL server is reachable by design; it retries)"
    else
        tail_log="$(docker logs --tail 5 "${FL_CTR}" 2>&1 | tr '\n' ' ' | cut -c1-240)"
        record "fl client running" false "${FL_CTR} not steady (${first} -> ${second}); last log: ${tail_log}"
    fi
fi

if [ "${SELFTEST_KEEP_UP:-0}" != 1 ]; then
    make -C "${REPO_ROOT}/trust" down-trust KIT=SELFTEST PROD=true FL_BACKEND="${BACKEND}" >/dev/null 2>&1 || true
fi

FINISHED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 - "${CHECKS_FILE}" "${OUT}" "${BACKEND}" "${STARTED}" "${FINISHED}" "${STAMP}" <<'PY'
import json
import sys
from pathlib import Path

checks_file, out, backend, started, finished, stamp = sys.argv[1:]
checks = []
for line in Path(checks_file).read_text().splitlines():
    name, ok, detail = (line.split("\t", 2) + [""])[:3]
    checks.append({"name": name, "ok": ok == "true", "detail": detail})
report = {
    "backend": backend,
    "started": started,
    "finished": finished,
    "ok": bool(checks) and all(c["ok"] for c in checks),
    "checks": checks,
}
base = Path(out) / f"selftest-{backend}-{stamp}"
base.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
rows = "\n".join(f"| {'✅' if c['ok'] else '❌'} | {c['name']} | {c['detail']} |" for c in checks)
status = "PASSED" if report["ok"] else "FAILED"
md = f"# Node self-test: {backend}\n\n{status} · {started} → {finished}\n\n| | Check | Detail |\n|---|---|---|\n{rows}\n"
base.with_suffix(".md").write_text(md)
(Path(out) / f"latest-{backend}.md").write_text(md)
print(f"Report: {base}.md")
sys.exit(0 if report["ok"] else 1)
PY
