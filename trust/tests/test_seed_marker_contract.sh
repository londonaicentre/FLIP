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

# The seed-marker contract behind `make -C trust ensure-seeded` (FLIP#1187).
#
# Three Makefiles, in three styles, must agree on one file and one payload per
# store or bring-up misbehaves silently in one of two directions: a disagreement
# means either re-fetching and re-posting ~2 GB of DICOM on every `make up`, or
# never re-seeding after a .data_version bump while every log line says the trust
# is up to date. The READER is trust/Makefile's ensure-seeded (marker paths in make,
# payload from PROJECTS / SOURCE_TRUST / TRUST_DATA_REVISION); the WRITERS are
# trust/omop-db/Makefile's seed-omop (`$(dir $(SEED_DATA_DIR)).seeded`) and
# trust/orthanc/Makefile's seed-orthanc (`$(dir …).$(notdir …).seeded`, its version
# resolved in shell from ../.data_version). They had drifted once before, with
# seeding trust 2 marking trust 1.
#
# So nothing here restates a marker path or a payload. The harness ASKS THE MAKEFILES:
#   - `make -n ensure-seeded` prints the reader's `cat '<marker>'` lines and, with no
#     marker present, the exact `make -C ./omop-db …` / `make -C ./orthanc …`
#     invocations it dispatches — the WHOLE argument set, continuation lines joined,
#     never one grepped line (a truncated line re-derives the writers' defaults);
#   - each of those invocations is re-run under -n to obtain the writer's own
#     `printf … > <marker>` line, which is then EXECUTED in the writer's directory.
# The marker on disk is therefore exactly what the writer would have produced, and
# the assertion is simply that the reader then reports "already seeded" — path and
# payload agreement in one observation — and that changing PROJECTS or the data
# revision makes it dispatch a re-seed, with CLEAR=1 reaching seed-orthanc.
#
# Everything runs against the real Makefiles in this checkout (dry-run, nothing is
# downloaded, no container is touched): the throwaway kits point both stores at a
# temp directory, and the default-layout kits are compared on paths only, so no
# marker is ever written inside the repository. Needs no Postgres, Orthanc or network.
#
# Usage (from trust/, as `make -C trust test-trust-data-tools` does):
#     bash tests/test_seed_marker_contract.sh

set -u

TRUST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${TRUST_DIR}"

# A parent make (test-trust-data-tools) would otherwise hand its flags to every
# nested make here.
unset MAKEFLAGS MFLAGS MAKELEVEL

TEST_ROOT="$(mktemp -d)"
KITS=()
cleanup() {
    local kit
    for kit in "${KITS[@]:-}"; do
        [[ -n "${kit}" ]] && rm -f "${TRUST_DIR}/.env.${kit}.development"
    done
    rm -rf "${TEST_ROOT}"
}
trap cleanup EXIT

PASSED=0
FAILED=0
ok() { echo "  ✓ $1"; PASSED=$((PASSED + 1)); }
no() { echo "  ✗ $1"; FAILED=$((FAILED + 1)); }
check() { if [[ "$2" == "$3" ]]; then ok "$1"; else no "$1 (expected '$3', got '$2')"; fi; }
contains() { if grep -qF -- "$3" <<<"$2"; then ok "$1"; else no "$1 (not found: $3)"; fi; }
lacks() { if grep -qF -- "$3" <<<"$2"; then no "$1 (found: $3)"; else ok "$1"; fi; }

# ── kits ────────────────────────────────────────────────────────────────────
# A throwaway kit file in trust/ (gitignored: .env.*), named so it cannot collide
# with a real one. Slot N, both stores under TEST_ROOT unless `default` asks for the
# kit-less layout the Makefiles fall back to.
new_kit() {
    local name="$1" slot="$2" layout="${3:-tmp}" file="${TRUST_DIR}/.env.$1.development"
    {
        printf 'FL_KIT_SLOT_NUMBER=%s\nTRUST_CODE=%s\n' "${slot}" "${name}"
        printf 'OMOP_DB_PORT=5499\nPACS_UI_PORT=8099\n'
        printf 'OMOP_POSTGRES_USER=u\nOMOP_POSTGRES_PASSWORD=p\nOMOP_POSTGRES_DB=d\n'
        printf 'ORTHANC_USERNAME=o\nORTHANC_PASSWORD=p\n'
        if [[ "${layout}" == "tmp" ]]; then
            printf 'OMOP_DATA_DIR=%s/%s/omop/db_data\n' "${TEST_ROOT}" "${name}"
            printf 'ORTHANC_STORAGE_DIR=%s/%s/pacs/orthanc-storage\n' "${TEST_ROOT}" "${name}"
        fi
    } > "${file}"
    # The stores exist before a seed runs (ensure_data_dirs creates them at bring-up), and
    # the Orthanc writer relies on that: its marker goes beside the store, with no mkdir.
    [[ "${layout}" == "tmp" ]] && mkdir -p "${TEST_ROOT}/${name}/omop/db_data" "${TEST_ROOT}/${name}/pacs/orthanc-storage"
    KITS+=("${name}")
}

# The reader, dry-run. FL_BACKEND is what CI passes; MAIN_ENV_FILE points at nothing so
# a dev box's hub env cannot leak into the kit. `$(MAKE)` lines still run under -n, so
# the recipe's own marker comparison happens for real and the dispatched sub-makes
# print their invocations rather than seeding anything.
dry_ensure() {
    local kit="$1" projects="$2"
    shift 2
    make -n ensure-seeded KIT="${kit}" PROJECTS="${projects}" FL_BACKEND=nvflare \
        MAIN_ENV_FILE=.seed-marker-contract-no-hub-env "$@" 2>&1
}

# The complete `make -C ./<component> seed-… …` invocation from a dry-run transcript,
# with its backslash continuations joined into one command.
dispatched() {
    local transcript="$1" component="$2"
    awk -v start="make -C ./${component} " '
        $0 ~ "^" start { cmd = $0; while (sub(/\\$/, "", cmd) && (getline line) > 0) cmd = cmd line; print cmd; exit }
    ' <<<"${transcript}"
}

# The writer's own marker line: re-run the dispatched invocation under -n (make is
# shadowed so the printed command runs dry) and keep the line that redirects the
# projects/source_trust/version payload into a .seeded file.
writer_marker_line() {
    local cmd="$1"
    (
        make() { command make -n "$@"; }
        eval "${cmd}" 2>&1
    ) | grep -F "printf 'projects=%s" | grep -F '.seeded' | head -1
}

# The marker path a writer line redirects into (`> path` or `> "path"`), absolute.
writer_path() {
    [[ "$1" =~ \>\ *\"?([^\"\ ]*\.seeded) ]] && realpath -m "${BASH_REMATCH[1]}"
}
# Every marker the reader `cat`s in a dry-run transcript, absolute, sorted.
reader_paths() {
    grep -o "cat '[^']*\.seeded'" <<<"$1" | sed "s/^cat '//; s/'\$//" \
        | while read -r p; do realpath -m "${p}"; done | sort -u
}
# Whether the reader's own echo (not the recipe text that quotes it) reported a store
# as already seeded: the executed line starts with the tick, the printed recipe with `echo`.
reported_seeded() { grep -q "^✅ $2 ($3) already seeded" <<<"$1"; }

# Execute the writer's marker line where the writer would (its own directory —
# ../.data_version and mkdir -p resolve relative to it). Refuses anything not under
# TEST_ROOT so a contract bug can never plant a marker in the checkout.
write_marker() {
    local component="$1" line="$2" path
    path="$(writer_path "${line}")"
    if [[ "${path}" != "${TEST_ROOT}/"* ]]; then
        no "${component}: writer would put the marker outside the sandbox (${path})"
        return 1
    fi
    (cd "${TRUST_DIR}/${component}" && eval "${line}") >/dev/null 2>&1
    [[ -f "${path}" ]] && printf '%s\n' "${path}"
}

# Seed both stores of a kit the way ensure-seeded would, via the writers' own lines.
# Prints the two marker paths (omop, orthanc).
seed_via_writers() {
    local kit="$1" projects="$2" transcript cmd line
    shift 2
    transcript="$(dry_ensure "${kit}" "${projects}" "$@")"
    for component in omop-db orthanc; do
        cmd="$(dispatched "${transcript}" "${component}")"
        if [[ -z "${cmd}" ]]; then no "${component}: ensure-seeded dispatched no seed for ${kit}"; return 1; fi
        line="$(writer_marker_line "${cmd}")"
        if [[ -z "${line}" ]]; then no "${component}: the dispatched invocation prints no marker line"; return 1; fi
        write_marker "${component}" "${line}" || return 1
    done
}

# ── 1. writer → reader round trip on slot 1 ─────────────────────────────────
echo "slot 1: the reader honours what the writers wrote"
new_kit SEEDCONTRACT1 1
out="$(dry_ensure SEEDCONTRACT1 "spleen_project")"
omop_cmd="$(dispatched "${out}" omop-db)"
pacs_cmd="$(dispatched "${out}" orthanc)"
contains "an unseeded trust dispatches seed-omop" "${omop_cmd}" "seed-omop TRUST_INDEX=1 SOURCE_TRUST=1"
contains "an unseeded trust dispatches seed-orthanc" "${pacs_cmd}" "seed-orthanc TRUST_INDEX=1 SOURCE_TRUST=1"
lacks "  ...without CLEAR=1 (no marker to supersede)" "${pacs_cmd}" " CLEAR=1"
if reported_seeded "${out}" OMOP SEEDCONTRACT1 || reported_seeded "${out}" PACS SEEDCONTRACT1; then
    no "  ...and reports nothing as already seeded"; else ok "  ...and reports nothing as already seeded"; fi

mapfile -t markers1 < <(seed_via_writers SEEDCONTRACT1 "spleen_project")
check "both writers put a marker in the sandbox" "${#markers1[@]}" "2"
check "the reader inspects exactly those two files" \
    "$(printf '%s\n' "${markers1[@]}" | sort -u | tr '\n' ' ')" "$(reader_paths "${out}" | tr '\n' ' ')"

out="$(dry_ensure SEEDCONTRACT1 "spleen_project")"
reported_seeded "${out}" OMOP SEEDCONTRACT1 && ok "then OMOP is already seeded" || no "then OMOP is already seeded"
reported_seeded "${out}" PACS SEEDCONTRACT1 && ok "then PACS is already seeded" || no "then PACS is already seeded"
check "  ...so seed-omop is not dispatched" "$(dispatched "${out}" omop-db)" ""
check "  ...nor seed-orthanc" "$(dispatched "${out}" orthanc)" ""

# ── 2. a changed PROJECTS re-seeds, clearing Orthanc first ──────────────────
echo "slot 1: a different PROJECTS supersedes the markers"
out="$(dry_ensure SEEDCONTRACT1 "spleen_project cxr_project")"
omop_cmd="$(dispatched "${out}" omop-db)"
pacs_cmd="$(dispatched "${out}" orthanc)"
contains "seed-omop is dispatched with the new list" "${omop_cmd}" 'PROJECTS="spleen_project cxr_project"'
contains "seed-orthanc is dispatched with the new list" "${pacs_cmd}" 'PROJECTS="spleen_project cxr_project"'
contains "  ...and with CLEAR=1, since a marker was there" "${pacs_cmd}" " CLEAR=1"
lacks "  ...while OMOP does not clear (its loader replaces the rows itself)" "${omop_cmd}" "CLEAR"

# ── 3. the data revision is resolved the same way by all three ──────────────
echo "slot 1: HF_TRUST_DATA_REVISION reaches the reader and both writers alike"
out="$(HF_TRUST_DATA_REVISION=main dry_ensure SEEDCONTRACT1 "spleen_project")"
contains "an overridden revision supersedes a pinned marker" "$(dispatched "${out}" omop-db)" "seed-omop TRUST_INDEX=1"
contains "  ...clearing Orthanc first" "$(dispatched "${out}" orthanc)" " CLEAR=1"
export HF_TRUST_DATA_REVISION=main
mapfile -t markers_main < <(seed_via_writers SEEDCONTRACT1 "spleen_project")
check "both writers re-marked under the override" "${#markers_main[@]}" "2"
check "  ...recording version=main (omop)" "$(grep -c '^version=main$' "${markers_main[0]}")" "1"
check "  ...recording version=main (orthanc)" "$(grep -c '^version=main$' "${markers_main[1]:-/dev/null}")" "1"
out="$(dry_ensure SEEDCONTRACT1 "spleen_project")"
if reported_seeded "${out}" OMOP SEEDCONTRACT1 && grep -q "^✅ PACS (SEEDCONTRACT1) already seeded: spleen_project @ main" <<<"${out}"; then
    ok "  ...which the reader accepts under the same override"; else no "  ...which the reader accepts under the same override"; fi
unset HF_TRUST_DATA_REVISION
out="$(dry_ensure SEEDCONTRACT1 "spleen_project")"
contains "  ...and re-seeds once the override is dropped" "$(dispatched "${out}" orthanc)" " CLEAR=1"

# ── 4. slot 2 marks slot 2 ───────────────────────────────────────────────────
echo "slot 2: its markers are its own"
seed_via_writers SEEDCONTRACT1 "spleen_project" >/dev/null   # back at the pinned version
new_kit SEEDCONTRACT2 2
mapfile -t markers2 < <(seed_via_writers SEEDCONTRACT2 "spleen_project")
check "both writers put a marker in the sandbox" "${#markers2[@]}" "2"
if [[ "${markers2[0]:-}" != "${markers1[0]:-}" && "${markers2[1]:-}" != "${markers1[1]:-}" ]]; then
    ok "seeding slot 2 marks slot 2, not slot 1"
else
    no "seeding slot 2 marks slot 1 (${markers2[*]} vs ${markers1[*]})"
fi
out="$(dry_ensure SEEDCONTRACT2 "spleen_project")"
if reported_seeded "${out}" OMOP SEEDCONTRACT2 && reported_seeded "${out}" PACS SEEDCONTRACT2; then
    ok "slot 2's reader accepts slot 2's markers"; else no "slot 2's reader accepts slot 2's markers"; fi
out="$(dry_ensure SEEDCONTRACT1 "spleen_project")"
if reported_seeded "${out}" OMOP SEEDCONTRACT1 && reported_seeded "${out}" PACS SEEDCONTRACT1; then
    ok "  ...and slot 1's are untouched"; else no "  ...and slot 1's are untouched"; fi
check "  ...with source_trust=2 in the payload" "$(grep -c '^source_trust=2$' "${markers2[0]:-/dev/null}")" "1"

# ── 5. the default (kit-less) layout, compared on paths only ────────────────
# Real dev trusts use this layout, so no marker is written: the writers are asked
# directly (seed-omop / seed-orthanc) and their paths compared with the reader's.
echo "default layout: reader and writers name the same files"
declare -A default_path
for slot in 1 2; do
    kit="SEEDCONTRACTD${slot}"
    new_kit "${kit}" "${slot}" default
    reads="$(reader_paths "$(dry_ensure "${kit}" "spleen_project")")"
    check "slot ${slot}: the reader inspects two markers" "$(wc -l <<<"${reads}")" "2"
    for component in omop-db orthanc; do
        target="seed-${component%-db}"
        cmd="$(dispatched "$(make -n "${target}" KIT="${kit}" PROJECTS="spleen_project" FL_BACKEND=nvflare \
            MAIN_ENV_FILE=.seed-marker-contract-no-hub-env 2>&1)" "${component}")"
        wpath="$(writer_path "$(writer_marker_line "${cmd}")")"
        if [[ -n "${wpath}" ]] && grep -qxF -- "${wpath}" <<<"${reads}"; then
            ok "slot ${slot}: ${target} writes where the reader looks (${wpath#"${TRUST_DIR}"/})"
        else
            no "slot ${slot}: ${target} writes '${wpath}', the reader looks at: $(tr '\n' ' ' <<<"${reads}")"
        fi
        default_path["${component}${slot}"]="${wpath}"
    done
done
for component in omop-db orthanc; do
    if [[ -n "${default_path[${component}1]}" && "${default_path[${component}1]}" != "${default_path[${component}2]}" ]]; then
        ok "${component}: slot 2's default marker is not slot 1's"
    else
        no "${component}: slot 1 and slot 2 share a default marker (${default_path[${component}1]})"
    fi
done

echo "==== ${PASSED} passed, ${FAILED} failed ===="
[[ "${FAILED}" -eq 0 ]]
