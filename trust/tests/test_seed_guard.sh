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

# Black-box tests for the .seeded guard (FLIP#1100) in update_omop_data.sh and
# update_orthanc_data.sh — the only thing standing between a trust/.data_version
# bump and the loss of a seeded volume, which on the OMOP side also carries the
# licensed vocabulary load.
#
# The guard's correctness rests on two INDEPENDENTLY WRITTEN path expressions
# agreeing: the marker the make target writes and the marker the update script
# looks for. They are written in different languages, in different directories,
# and deliberately differ between the two pairs — the OMOP marker sits BESIDE
# db_data, the Orthanc one INSIDE the storage dir. Nothing checked that they
# pointed at the same file, and they did not: seed-orthanc derived its path from
# the unsuffixed ORTHANC_STORAGE_DIR, which the kit-example include pins to slot
# 1, so seeding trust 2 marked trust 1.
#
# So this harness never hardcodes a marker path. It ASKS THE MAKE TARGET where
# the marker goes (`make -n`, the writer), puts one there, and then asserts the
# UPDATE SCRIPT (the reader) honours it. A disagreement between the two shows up
# as a guard that fails to fire, which is exactly the production failure.
#
# Both halves are the real code. Each case builds a sandbox holding copies of
# the two scripts, the two Makefiles and the kit example, laid out as trust/ —
# the scripts resolve their pin (../.data_version), their cache (./volumes) and
# their TRUST_DIR relative to that, so a run touches nothing outside the sandbox
# and no live dev volume is ever at risk. curl, tar and sudo are stubbed on PATH
# so a case can assert that a refusal happened BEFORE the ~1 GB download, not
# after it.
#
# Usage:
#     bash trust/tests/test_seed_guard.sh

set -u

REPO_TRUST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

# ── stubs ────────────────────────────────────────────────────────────────────
# Each logs its invocation and then does the minimum the script expects, so the
# happy path completes and a case can assert on what was and wasn't called.
MOCKBIN="${TEST_ROOT}/mockbin"
mkdir -p "${MOCKBIN}"

cat > "${MOCKBIN}/curl" <<'MOCK_CURL'
#!/usr/bin/env bash
echo "curl $*" >> "${CURL_LOG}"
out="" prev=""
for arg in "$@"; do
    [[ "${prev}" == "-o" ]] && out="${arg}"
    prev="${arg}"
done
if [[ -n "${out}" ]]; then
    mkdir -p "$(dirname "${out}")"
    printf 'stub archive\n' > "${out}"
fi
MOCK_CURL

cat > "${MOCKBIN}/tar" <<'MOCK_TAR'
#!/usr/bin/env bash
echo "tar $*" >> "${TAR_LOG}"
dest="" prev=""
for arg in "$@"; do
    [[ "${prev}" == "-C" ]] && dest="${arg}"
    prev="${arg}"
done
if [[ -n "${dest}" ]]; then
    mkdir -p "${dest}"
    printf 'from the snapshot\n' > "${dest}/RESTORED"
fi
MOCK_TAR

# sudo runs the command for real: everything it can reach is inside the sandbox,
# and letting the rm actually happen is what makes "FORCE=1 replaces the seeded
# volume" an observation rather than an assumption.
cat > "${MOCKBIN}/sudo" <<'MOCK_SUDO'
#!/usr/bin/env bash
echo "sudo $*" >> "${SUDO_LOG}"
exec "$@"
MOCK_SUDO

chmod +x "${MOCKBIN}"/*
PATH="${MOCKBIN}:${PATH}"
export PATH

PASSED=0
FAILED=0
ok() { echo "  ✓ $1"; PASSED=$((PASSED + 1)); }
no() { echo "  ✗ $1"; FAILED=$((FAILED + 1)); }
check() { if [[ "$2" == "$3" ]]; then ok "$1"; else no "$1 (expected '$3', got '$2')"; fi; }

# ── sandbox ──────────────────────────────────────────────────────────────────
# A trust/ tree holding the real scripts and Makefiles. PINNED_VERSION is what
# trust/.data_version says; LOCAL_VERSION is what the trust already holds, so
# the two differing is the "pin moved" condition the guard exists for.
new_sandbox() {
    local pinned_version="$1"
    SANDBOX="$(mktemp -d "${TEST_ROOT}/case.XXXXXX")"
    mkdir -p "${SANDBOX}/trust/omop-db" "${SANDBOX}/trust/orthanc"
    printf '%s\n' "${pinned_version}" > "${SANDBOX}/trust/.data_version"
    cp "${REPO_TRUST_DIR}/omop-db/update_omop_data.sh" "${SANDBOX}/trust/omop-db/"
    cp "${REPO_TRUST_DIR}/omop-db/Makefile" "${SANDBOX}/trust/omop-db/"
    cp "${REPO_TRUST_DIR}/orthanc/update_orthanc_data.sh" "${SANDBOX}/trust/orthanc/"
    cp "${REPO_TRUST_DIR}/orthanc/Makefile" "${SANDBOX}/trust/orthanc/"
    cp "${REPO_TRUST_DIR}/.env.GSTT.development.example" "${SANDBOX}/trust/"
    CURL_LOG="${SANDBOX}/curl.log"
    TAR_LOG="${SANDBOX}/tar.log"
    SUDO_LOG="${SANDBOX}/sudo.log"
    : > "${CURL_LOG}"
    : > "${TAR_LOG}"
    : > "${SUDO_LOG}"
    export CURL_LOG TAR_LOG SUDO_LOG
}

# Where the WRITER puts the marker, asked of the make target itself rather than
# restated here. Prints an absolute path (the recipe's redirect is relative to
# the Makefile's own directory, which is where the recipe runs).
marker_from_make() {
    local component="$1" target="$2" trust_index="$3" printed
    printed="$(cd "${SANDBOX}/trust/${component}" \
        && make -n "${target}" TRUST_INDEX="${trust_index}" 2>/dev/null \
        | grep -o '> *"\?[^" ]*\.seeded' | tail -1 | sed 's/^> *"\?//')"
    [[ -z "${printed}" ]] && return 1
    (cd "${SANDBOX}/trust/${component}" && realpath -m "${printed}")
}

# The trust's data dir, in each pair's own default layout.
data_dir() {
    case "$1" in
    omop-db) printf '%s\n' "${SANDBOX}/trust/omop-db/volumes/Trust_$2/db_data" ;;
    orthanc) printf '%s\n' "${SANDBOX}/trust/orthanc/orthanc-storage-trust$2" ;;
    esac
}

script_of() {
    case "$1" in
    omop-db) printf '%s\n' "update_omop_data.sh" ;;
    orthanc) printf '%s\n' "update_orthanc_data.sh" ;;
    esac
}

# Give a trust a populated data dir already at LOCAL_VERSION, so the only reason
# to re-snapshot is the pin having moved.
seed_volume() {
    local component="$1" trust_index="$2" local_version="$3" dir
    dir="$(data_dir "${component}" "${trust_index}")"
    mkdir -p "${dir}" "${SANDBOX}/trust/${component}/volumes"
    printf 'seeded rows the snapshot does not have\n' > "${dir}/SEEDED_CONTENT"
    printf '%s\n' "${local_version}" \
        > "${SANDBOX}/trust/${component}/volumes/.local_data_version_trust${trust_index}"
}

run_update() {
    local component="$1" trust_index="$2"
    shift 2
    (cd "${SANDBOX}/trust/${component}" && env "$@" TRUST="${trust_index}" bash "./$(script_of "${component}")") \
        > "${SANDBOX}/out.log" 2>&1
}

# ── cases ────────────────────────────────────────────────────────────────────
for component in omop-db orthanc; do
    case "${component}" in
    omop-db) target="seed-omop" ;;
    orthanc) target="seed-orthanc" ;;
    esac

    echo "${component} / $(script_of "${component}")"

    # 1. marker present + pin moved -> refuses, before the download
    new_sandbox 20260901
    seed_volume "${component}" 1 20260729
    marker="$(marker_from_make "${component}" "${target}" 1)" || marker=""
    if [[ -z "${marker}" ]]; then
        no "the make target prints a marker path"
    else
        ok "the make target prints a marker path ($(basename "$(dirname "${marker}")")/.seeded)"
        mkdir -p "$(dirname "${marker}")"
        printf 'projects=spleen_project\nversion=20260729\n' > "${marker}"

        run_update "${component}" 1
        status=$?
        check "a seeded volume refuses a pin bump" "${status}" "1"
        check "  ...before any download" "$(wc -l < "${CURL_LOG}")" "0"
        check "  ...leaving the seeded content in place" \
            "$(cat "$(data_dir "${component}" 1)/SEEDED_CONTENT" 2>/dev/null)" \
            "seeded rows the snapshot does not have"
        grep -q "FORCE=1" "${SANDBOX}/out.log" \
            && ok "  ...and says how to override" \
            || no "  ...and says how to override"
    fi

    # 2. FORCE=1 falls through
    new_sandbox 20260901
    seed_volume "${component}" 1 20260729
    marker="$(marker_from_make "${component}" "${target}" 1)"
    mkdir -p "$(dirname "${marker}")"
    printf 'projects=spleen_project\nversion=20260729\n' > "${marker}"

    run_update "${component}" 1 FORCE=1
    check "FORCE=1 falls through" "$?" "0"
    check "  ...downloading the snapshot" "$(wc -l < "${CURL_LOG}")" "1"
    check "  ...and replacing the volume" \
        "$(cat "$(data_dir "${component}" 1)/RESTORED" 2>/dev/null)" "from the snapshot"

    # 3. no marker behaves as before
    new_sandbox 20260901
    seed_volume "${component}" 1 20260729

    run_update "${component}" 1
    check "an unseeded volume updates as before" "$?" "0"
    check "  ...downloading the snapshot" "$(wc -l < "${CURL_LOG}")" "1"

    # 4. a marker belongs to ONE trust — the wrong-trust bug this guard shipped with
    new_sandbox 20260901
    seed_volume "${component}" 1 20260729
    seed_volume "${component}" 2 20260729
    marker_1="$(marker_from_make "${component}" "${target}" 1)"
    marker_2="$(marker_from_make "${component}" "${target}" 2)"
    if [[ "${marker_1}" == "${marker_2}" ]]; then
        no "seeding trust 2 marks trust 2, not trust 1 (both resolve to ${marker_1})"
    else
        ok "seeding trust 2 marks trust 2, not trust 1"
    fi
    mkdir -p "$(dirname "${marker_2}")"
    printf 'projects=spleen_project\nversion=20260729\n' > "${marker_2}"

    run_update "${component}" 2
    check "  ...so trust 2 is protected" "$?" "1"
    run_update "${component}" 1
    check "  ...and trust 1, unseeded, still updates" "$?" "0"

    echo
done

echo "==== ${PASSED} passed, ${FAILED} failed ===="
[[ "${FAILED}" -eq 0 ]]
