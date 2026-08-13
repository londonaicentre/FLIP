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

# Black-box tests for files/load_core_vocab.sh — the --check probe, the two-pass
# load, and the fail-fast ordering.
#
# Drives the REAL script with `psql` stubbed on PATH (no Postgres, no bundle, no
# credentials). The stub answers the per-table guard queries from PRESENT_TABLES
# and appends every COPY / -f invocation to a log, so a case can assert not just
# the exit status but exactly which tables were written and in what order.
#
# These exit codes are load-bearing beyond this script: the Kubernetes
# omop-vocab-load Job decides whether to download the multi-GB licensed bundle
# purely from `--check`'s status, and a wrong 0 there leaves a trust with no
# vocabulary — a state in which every pod is healthy and only cohort queries
# joining omop.concept come back empty.
#
# Usage:
#     bash trust/omop-db/tests/test_load_core_vocab.sh

set -u

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/files/load_core_vocab.sh"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

MOCKBIN="${TEST_ROOT}/mockbin"
mkdir -p "${MOCKBIN}"

# Mock psql. Behaviour is driven per case by the environment:
#   PRESENT_TABLES — space-separated lowercase table names whose guard returns 't'
#   PSQL_FAIL      — non-empty: exit 2 without answering (unreachable database)
#   PSQL_GARBAGE   — non-empty: answer guards with neither 't' nor 'f'
#   PSQL_LOG       — file receiving one line per COPY / -f invocation
# Guard queries name the table lowercase (omop.concept), COPY names it uppercase
# (omop.CONCEPT), so the table is matched case-insensitively.
cat > "${MOCKBIN}/psql" <<'MOCK_PSQL'
#!/usr/bin/env bash
if [[ -n "${PSQL_FAIL:-}" ]]; then
    echo "psql: error: connection to server failed: Connection refused" >&2
    exit 2
fi
args=("$@")
mode="" payload=""
for ((i = 0; i < ${#args[@]}; i++)); do
    case "${args[i]}" in
        -tAc) mode="query"; payload="${args[i + 1]:-}" ;;
        -c)   mode="copy";  payload="${args[i + 1]:-}" ;;
        -f)   mode="file";  payload="${args[i + 1]:-}" ;;
    esac
done
case "${mode}" in
    query)
        if [[ -n "${PSQL_GARBAGE:-}" ]]; then echo "surprise"; exit 0; fi
        table="$(printf '%s' "${payload}" | grep -oiE 'omop\.[a-z_]+' | head -1 | cut -d. -f2 | tr '[:upper:]' '[:lower:]')"
        if [[ " ${PRESENT_TABLES:-} " == *" ${table} "* ]]; then echo "t"; else echo "f"; fi
        ;;
    copy)
        table="$(printf '%s' "${payload}" | grep -oiE 'omop\.[a-z_]+' | head -1 | cut -d. -f2 | tr '[:upper:]' '[:lower:]')"
        echo "COPY ${table}" >> "${PSQL_LOG}"
        ;;
    file)
        echo "FILE ${payload}" >> "${PSQL_LOG}"
        ;;
esac
exit 0
MOCK_PSQL
chmod +x "${MOCKBIN}/psql"

ALL_TABLES="concept_ancestor concept_class concept_relationship concept_synonym concept domain drug_strength relationship vocabulary"

FAILURES=0
CASE=""

fail() {
    echo "  ✗ ${CASE}: $1"
    FAILURES=$((FAILURES + 1))
}

pass() {
    echo "  ✓ ${CASE}"
}

# Runs the real script with the stub on PATH. Sets RC and LOG for assertions;
# OUT captures stdout+stderr. A bundle dir is only created when a case asks for
# one, so "the directory does not exist" is itself an exercisable state.
run_script() {
    PSQL_LOG="${TEST_ROOT}/psql.log"
    : > "${PSQL_LOG}"
    # Every variable the script reads is set here, OMOP_DB_HOST included: under
    # `make local_test` the kit env file's keys are all exported into this
    # process, and a case must not depend on which of them happen to be set.
    OUT="$(PATH="${MOCKBIN}:${PATH}" OMOP_DB_HOST=localhost \
        OMOP_DB_PORT=5432 OMOP_POSTGRES_USER=u OMOP_POSTGRES_PASSWORD=p OMOP_POSTGRES_DB=d \
        PRESENT_TABLES="${PRESENT_TABLES:-}" PSQL_FAIL="${PSQL_FAIL:-}" \
        PSQL_GARBAGE="${PSQL_GARBAGE:-}" PSQL_LOG="${PSQL_LOG}" \
        bash "${SCRIPT}" "$@" 2>&1)"
    RC=$?
    LOG="$(cat "${PSQL_LOG}")"
}

# Builds a bundle directory holding a CSV per named table (uppercase filenames,
# as the real bundle ships them). No argument builds a complete bundle.
make_bundle() {
    local dir="${TEST_ROOT}/bundle"
    rm -rf "${dir}"
    mkdir -p "${dir}"
    local tables="${1:-${ALL_TABLES}}"
    local t
    for t in ${tables}; do
        echo "header" > "${dir}/$(printf '%s' "${t}" | tr '[:lower:]' '[:upper:]').csv"
    done
    printf '%s' "${dir}"
}

echo "load_core_vocab.sh"

# ── --check ────────────────────────────────────────────────────────────────
# The Job writes its skip marker on 0 and fetches on anything else, so 0 must
# mean "every table present" and nothing weaker.

CASE="--check exits 0 when every table already holds the core vocabulary"
PRESENT_TABLES="${ALL_TABLES}" PSQL_FAIL="" PSQL_GARBAGE="" run_script --check
if [[ ${RC} -ne 0 ]]; then fail "expected exit 0, got ${RC}"
elif [[ -n "${LOG}" ]]; then fail "--check must not write to the database, logged: ${LOG}"
else pass; fi

CASE="--check exits non-zero when a single table is missing"
PRESENT_TABLES="${ALL_TABLES/concept_synonym/}" PSQL_FAIL="" PSQL_GARBAGE="" run_script --check
if [[ ${RC} -eq 0 ]]; then fail "exited 0 with concept_synonym absent — the Job would skip the fetch and leave the trust vocabulary-less"
elif [[ "${OUT}" != *"CONCEPT_SYNONYM"* ]]; then fail "did not name the pending table: ${OUT}"
else pass; fi

CASE="--check exits non-zero when nothing is loaded at all"
PRESENT_TABLES="" PSQL_FAIL="" PSQL_GARBAGE="" run_script --check
if [[ ${RC} -eq 0 ]]; then fail "expected non-zero on an empty database"; else pass; fi

CASE="--check exits non-zero when the database is unreachable"
PRESENT_TABLES="${ALL_TABLES}" PSQL_FAIL="1" PSQL_GARBAGE="" run_script --check
if [[ ${RC} -eq 0 ]]; then fail "an unreachable database must never read as 'already loaded'"; else pass; fi

CASE="--check needs no bundle directory argument"
PRESENT_TABLES="${ALL_TABLES}" PSQL_FAIL="" PSQL_GARBAGE="" run_script --check
if [[ "${OUT}" == *"usage:"* ]]; then fail "tripped the usage guard: ${OUT}"; else pass; fi

# ── load ───────────────────────────────────────────────────────────────────

CASE="loads only the tables that are missing"
BUNDLE="$(make_bundle)"
PRESENT_TABLES="concept_ancestor concept_class concept_relationship concept_synonym domain drug_strength relationship vocabulary" \
    PSQL_FAIL="" PSQL_GARBAGE="" run_script "${BUNDLE}"
if [[ ${RC} -ne 0 ]]; then fail "expected exit 0, got ${RC}: ${OUT}"
elif [[ "${LOG}" != "COPY concept" ]]; then fail "expected exactly 'COPY concept', got: ${LOG}"
else pass; fi

CASE="applies the constraints after loading"
BUNDLE="$(make_bundle)"
PRESENT_TABLES="" PSQL_FAIL="" PSQL_GARBAGE="" run_script "${BUNDLE}" "${TEST_ROOT}/constraints.sql"
if [[ ${RC} -ne 0 ]]; then fail "expected exit 0, got ${RC}: ${OUT}"
elif [[ "$(printf '%s\n' "${LOG}" | grep -c '^COPY ')" -ne 9 ]]; then fail "expected 9 COPYs, got: ${LOG}"
elif [[ "$(printf '%s\n' "${LOG}" | tail -1)" != "FILE ${TEST_ROOT}/constraints.sql" ]]; then
    fail "constraints must be applied last, log ended: $(printf '%s\n' "${LOG}" | tail -1)"
else pass; fi

# The Kubernetes Job's no-bundle branch is legal only because of this: when the
# probe skipped the fetch there is no bundle on disk, and the loader must still
# reach the constraints instead of failing the whole Helm release.
CASE="no-op run needs no bundle on disk and still applies the constraints"
PRESENT_TABLES="${ALL_TABLES}" PSQL_FAIL="" PSQL_GARBAGE="" \
    run_script "${TEST_ROOT}/never-fetched" "${TEST_ROOT}/constraints.sql"
if [[ ${RC} -ne 0 ]]; then fail "expected exit 0 against an absent bundle dir, got ${RC}: ${OUT}"
elif [[ "${LOG}" == *"COPY"* ]]; then fail "must not load anything, logged: ${LOG}"
elif [[ "${LOG}" != "FILE ${TEST_ROOT}/constraints.sql" ]]; then fail "constraints were not applied: ${LOG}"
else pass; fi

# An incomplete bundle must abort before the first COPY. Loading half a
# vocabulary is worse than loading none: the guards would then read the
# half-loaded tables as present and never retry them.
CASE="an incomplete bundle aborts before any table is loaded"
BUNDLE="$(make_bundle "concept_ancestor concept_class")"
PRESENT_TABLES="" PSQL_FAIL="" PSQL_GARBAGE="" run_script "${BUNDLE}"
if [[ ${RC} -eq 0 ]]; then fail "expected non-zero on an incomplete bundle"
elif [[ -n "${LOG}" ]]; then fail "aborted too late — already wrote: ${LOG}"
elif [[ "${OUT}" != *"missing"* ]]; then fail "did not explain the missing file: ${OUT}"
else pass; fi

# "No bundle at all" and "a bundle missing a file" are different faults. The
# first means a caller that probed first skipped the fetch and then disagreed
# with its own probe; pointing the operator at an incomplete artifact that was
# never downloaded would send them to inspect the wrong thing.
CASE="an absent bundle directory is distinguished from an incomplete one"
PRESENT_TABLES="" PSQL_FAIL="" PSQL_GARBAGE="" run_script "${TEST_ROOT}/never-fetched"
if [[ ${RC} -eq 0 ]]; then fail "expected non-zero"
elif [[ -n "${LOG}" ]]; then fail "must not load anything, logged: ${LOG}"
elif [[ "${OUT}" != *"does not exist"* ]]; then fail "blamed an incomplete bundle instead: ${OUT}"
else pass; fi

# The CSV check is scoped to the tables actually being loaded, so a bundle that
# omits a file for an already-present table is not an error.
CASE="a bundle may omit CSVs for tables that are already present"
BUNDLE="$(make_bundle "concept")"
PRESENT_TABLES="${ALL_TABLES/concept /}" PSQL_FAIL="" PSQL_GARBAGE="" run_script "${BUNDLE}"
if [[ ${RC} -ne 0 ]]; then fail "expected exit 0, got ${RC}: ${OUT}"
elif [[ "${LOG}" != "COPY concept" ]]; then fail "expected exactly 'COPY concept', got: ${LOG}"
else pass; fi

# A guard that answers neither t nor f means the probe cannot be trusted. Falling
# through to the COPY would duplicate rows in the four tables with no primary key.
CASE="an unreadable guard result aborts instead of loading"
BUNDLE="$(make_bundle)"
PRESENT_TABLES="" PSQL_FAIL="" PSQL_GARBAGE="1" run_script "${BUNDLE}"
if [[ ${RC} -eq 0 ]]; then fail "expected non-zero on an unexpected guard result"
elif [[ -n "${LOG}" ]]; then fail "must not load on an unexpected guard result, logged: ${LOG}"
else pass; fi

CASE="an unreadable guard result fails --check too"
PRESENT_TABLES="" PSQL_FAIL="" PSQL_GARBAGE="1" run_script --check
if [[ ${RC} -eq 0 ]]; then fail "expected non-zero on an unexpected guard result"; else pass; fi

# ── usage ──────────────────────────────────────────────────────────────────

CASE="no arguments is a usage error"
PRESENT_TABLES="${ALL_TABLES}" PSQL_FAIL="" PSQL_GARBAGE="" run_script
if [[ ${RC} -eq 0 ]]; then fail "expected non-zero"
elif [[ "${OUT}" != *"usage:"* ]]; then fail "expected a usage message, got: ${OUT}"
else pass; fi

# Passed empty rather than left unset: trust/omop-db/Makefile exports every key
# of the kit env file into its recipes, so under `make local_test` a real
# OMOP_POSTGRES_PASSWORD is in this process's environment and an "unset" case
# would silently test nothing.
CASE="a missing connection variable is a hard error"
OUT="$(PATH="${MOCKBIN}:${PATH}" OMOP_DB_PORT=5432 OMOP_POSTGRES_USER=u OMOP_POSTGRES_DB=d \
    OMOP_POSTGRES_PASSWORD="" bash "${SCRIPT}" --check 2>&1)"
RC=$?
if [[ ${RC} -eq 0 ]]; then fail "expected non-zero with OMOP_POSTGRES_PASSWORD empty"
elif [[ "${OUT}" != *"OMOP_POSTGRES_PASSWORD"* ]]; then fail "did not name the missing variable: ${OUT}"
else pass; fi

echo
if [[ ${FAILURES} -gt 0 ]]; then
    echo "❌ ${FAILURES} failing case(s)"
    exit 1
fi
echo "✅ all cases passed"
