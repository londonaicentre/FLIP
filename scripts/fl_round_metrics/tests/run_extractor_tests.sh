#!/bin/bash
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
# Fixture tests for extract_simulator_round_timings.sh.
#
# Why these exist: the extractors' parsing layer is a pipeline of grep/sed/awk stages whose
# failures are silent — they produce a plausible-looking rounds.tsv rather than an error. Two
# such bugs have already shipped in this layer (a mawk OFMT number-formatting difference, and
# a grep filename-prefix that passed raw log lines through as round data). Neither is
# reachable by reading the code alone; both are caught instantly by diffing a known log
# against a known table.
#
# `--log` is the injection point: no AWS, no GPU, no simulator run needed. The platform
# extractor has no equivalent seam (it reads CloudWatch directly), but it shares the
# rounds.tsv schema and most of its awk, so the schema and stats assertions here cover the
# shared contract; test 4/5 drive that schema through --compare.
#
# Run: bash scripts/fl_round_metrics/tests/run_extractor_tests.sh
# CI:  .github/workflows/fl_round_metrics_tests.yml (ubuntu-latest, where awk is mawk)

set -euo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURES_DIR="${TESTS_DIR}/fixtures"
EXTRACTOR="$(cd "${TESTS_DIR}/.." && pwd)/extract_simulator_round_timings.sh"

# The fixtures' timestamps carry no timezone (real simulator logs don't either) and
# rounds.tsv records absolute epoch milliseconds, so the goldens only reproduce under a
# pinned zone.
export TZ=UTC

# Minimal PATH for extractor runs: the boxplot step shells out to `uv run --with pandas
# --with seaborn` when uv is on PATH, which would make these tests download ~100 MB and
# depend on the network. Hiding uv makes the plot step skip deterministically (it is
# explicitly best-effort) and also asserts the extractor needs nothing beyond coreutils.
MINIMAL_PATH="/usr/bin:/bin"

PASSED=0
FAILED=0
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

pass() { PASSED=$((PASSED + 1)); echo "  ok   — $1"; }
fail() { FAILED=$((FAILED + 1)); echo "  FAIL — $1" >&2; }

# run_extractor RUN_NAME FIXTURE_LOG [extra args...] — echoes the output directory and
# returns the extractor's own exit status. Capturing rc explicitly matters: `set -e` is
# suspended inside a function used as an `if` condition, so without this the trailing echo
# would set the return status to 0 and every "must fail" test would silently pass.
run_extractor() {
    local run_name="$1" fixture="$2"; shift 2
    local out_base="${TMP_ROOT}/${run_name}" rc=0
    PATH="$MINIMAL_PATH" "$EXTRACTOR" \
        --log "${FIXTURES_DIR}/${fixture}" \
        --run-name "$run_name" \
        -o "$out_base" \
        "$@" >"${TMP_ROOT}/${run_name}.stderr" 2>&1 || rc=$?
    echo "${out_base}/${run_name}"
    return "$rc"
}

assert_golden() {
    local label="$1" actual="$2" golden="$3"
    if diff -u "$golden" "$actual" > "${TMP_ROOT}/diff.txt" 2>&1; then
        pass "$label"
    else
        fail "$label — rounds.tsv differs from golden:"
        sed 's/^/      /' "${TMP_ROOT}/diff.txt" >&2
    fi
}

assert_contains() {
    local label="$1" file="$2" needle="$3"
    if grep -qF -- "$needle" "$file"; then
        pass "$label"
    else
        fail "$label — expected to find in $(basename "$file"): ${needle}"
    fi
}

echo "== extract_simulator_round_timings.sh fixture tests =="

# --------------------------------------------------------------------------------------
# 1. Happy path: three complete rounds, per-round contribution tags, one untagged
#    contribution that must count towards the totals but towards no individual round.
# --------------------------------------------------------------------------------------
echo "[1] three complete rounds"
OUT="$(run_extractor three-rounds simulator_three_rounds.log)"
assert_golden "rounds.tsv matches golden" "${OUT}/rounds.tsv" "${FIXTURES_DIR}/simulator_three_rounds.rounds.tsv"
assert_contains "summary reports 3 rounds" "${OUT}/summary.md" "**3 round(s) executed**"
# 2 (round 0) + 1 (round 1) + 2 (round 2) + 1 untagged = 6 accepted; 1 rejected.
assert_contains "summary totals count the untagged contribution" "${OUT}/summary.md" \
    "**6 accepted**, **1 rejected**"
assert_contains "summary reports the full span" "${OUT}/summary.md" "**96s**"

# Regression guard for the mawk OFMT class: epoch-ms columns are 13-digit integers, and any
# awk stage that coerced them numerically with `print` (default OFMT %.6g) would render them
# as 1.75285e+12. Scientific notation anywhere in the table means that bug is back.
if grep -qE 'e\+[0-9]' "${OUT}/rounds.tsv"; then
    fail "no scientific notation in rounds.tsv (mawk OFMT regression)"
else
    pass "no scientific notation in rounds.tsv (mawk OFMT regression)"
fi

# --------------------------------------------------------------------------------------
# 2. A round that never finishes (client dropped mid-round). Its duration must be "-", and
#    the inter-round gap chain must BREAK there rather than measuring the next round's gap
#    from two rounds back — which would silently fold the whole dead round into the gap.
# --------------------------------------------------------------------------------------
echo "[2] a round with no 'finished' event"
OUT="$(run_extractor missing-end simulator_missing_round_end.log)"
assert_golden "rounds.tsv matches golden" "${OUT}/rounds.tsv" \
    "${FIXTURES_DIR}/simulator_missing_round_end.rounds.tsv"
# Rounds 0->1 is the only measurable gap (2.000s). Round 2 has no end, so round 3's start
# has no valid predecessor: n must be 1, not 2, and the mean must not include the 18s
# that spans the incomplete round.
assert_contains "inter-round gap skips the broken chain link" "${OUT}/summary.md" \
    "| Inter-round gap | 2.000 | 0.000 | 1 |"

# --------------------------------------------------------------------------------------
# 3. Aborted run: rounds started, none ever finished. Span has no end to measure to and
#    must read n/a rather than a huge negative number from an unset awk max.
# --------------------------------------------------------------------------------------
echo "[3] aborted run, no round ever finished"
OUT="$(run_extractor no-end simulator_no_round_end.log)"
assert_contains "span reads n/a" "${OUT}/summary.md" \
    "Total span (first round start to last round end): **n/a**."
if grep -qE '\*\*-[0-9]+s?\*\*' "${OUT}/summary.md"; then
    fail "span is not a negative number"
else
    pass "span is not a negative number"
fi

# --------------------------------------------------------------------------------------
# 4. --compare rejects a file that isn't a rounds.tsv. An unrelated or truncated file would
#    otherwise render as a 0-round or garbage comparison table with full authority.
# --------------------------------------------------------------------------------------
echo "[4] --compare validates its input"
printf 'not\ta\trounds\ttsv\n1\t2\t3\t4\n' > "${TMP_ROOT}/bogus.tsv"
if run_extractor compare-bogus simulator_three_rounds.log --compare "${TMP_ROOT}/bogus.tsv" >/dev/null 2>&1; then
    fail "--compare rejects a non-rounds.tsv file"
else
    assert_contains "--compare rejects a non-rounds.tsv file" "${TMP_ROOT}/compare-bogus.stderr" \
        "does not look like a rounds.tsv"
fi

# --------------------------------------------------------------------------------------
# 5. --compare against a valid platform-shaped rounds.tsv renders the overhead table and
#    reports each side's n, so a 2-round smoke compare can't masquerade as a full run.
# --------------------------------------------------------------------------------------
echo "[5] --compare renders the overhead table with per-side n"
OUT="$(run_extractor compare-ok simulator_three_rounds.log \
    --compare "${FIXTURES_DIR}/platform_reference.rounds.tsv")"
assert_contains "comparison section present" "${OUT}/summary.md" \
    "## Platform vs simulator (steady-state rounds, i.e. round >= 1)"
# Platform fixture has 2 steady-state rounds (1 and 2); the simulator fixture has 2 as well.
assert_contains "round-duration row carries both n values" "${OUT}/summary.md" \
    "| Round duration | 120.000 ± 14.142 | 2 | 30.125 ± 0.530 | 2 | +89.875 |"
assert_contains "round 0 is compared separately from steady state" "${OUT}/summary.md" \
    "| Round 0 (initialisation) | 3500.000 | 1 | 32.350 | 1 | +3467.650 |"

echo
echo "== ${PASSED} passed, ${FAILED} failed =="
[[ "$FAILED" -eq 0 ]]
