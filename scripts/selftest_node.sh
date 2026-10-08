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
    KIT_DIR="${REPO_ROOT}/fl-services/nvflare/provision/workspace-dev"
else
    KIT_DIR="${REPO_ROOT}/fl-services/flower/provision/creds"
fi

echo "== Self-test (${BACKEND}) started ${STARTED}"
if [ "${SELFTEST_SKIP_PROVISION:-0}" != 1 ]; then
    if make -C "${REPO_ROOT}/fl-services/${BACKEND}" provision >/dev/null; then
        record "dev fl kit" true "${KIT_DIR}"
    else
        record "dev fl kit" false "make -C fl-services/${BACKEND} provision failed"
    fi
fi

if python3 "${REPO_ROOT}/scripts/selftest_kit.py" --backend "${BACKEND}" --fl-kit-dir "${KIT_DIR}" \
    --data-root "${DATA_ROOT}" --docker-tag "${DOCKER_TAG}" --out "${KIT_OUT}" --force >/dev/null; then
    record "kit file" true "${KIT_OUT}"
else
    record "kit file" false "selftest_kit.py failed"
fi

if make -C "${REPO_ROOT}/trust" up-trust KIT=SELFTEST PROD=true FL_BACKEND="${BACKEND}"; then
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
dicom_log_lines() { docker exec "${XNAT_CTR}" sh -c "wc -l < ${DICOM_LOG} 2>/dev/null || echo 0" 2>/dev/null || echo 0; }
before="$(dicom_log_lines)"
instance="$(curl -fsS -u "${ORTHANC_AUTH}" "http://127.0.0.1:8042/instances?since=0&limit=1" 2>/dev/null | tr -d '[]" \n')"
store="$(curl -fsS -u "${ORTHANC_AUTH}" -X POST "http://127.0.0.1:8042/modalities/XNAT/store" \
    -d "{\"Resources\":[\"${instance}\"],\"Synchronous\":true}" 2>/dev/null || true)"
after="$(dicom_log_lines)"
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

if docker ps --filter status=running --format '{{.Names}}' | grep -Eq 'fl-client|supernode'; then
    record "fl client running" true "container up (no FL server is reachable by design; it retries)"
else
    record "fl client running" false "no running fl-client or supernode container"
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
