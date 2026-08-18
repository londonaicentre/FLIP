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
# extract_simulator_round_timings.sh — local-simulator counterpart of extract_platform_round_timings.sh.
#
# Parses an NVFLARE SimEnv workspace's logs for the SAME controller events the production
# script pulls from CloudWatch (flip.nvflare.controllers.scatter_and_gather.ScatterAndGather
# emits identical lines in both settings):
#     "Round N started." / "Round N finished."
#     "Start aggregation." / "End aggregation."
#     "Contribution from <site> ACCEPTED|REJECTED by the aggregator at round <N>."
# and writes the same artefacts: rounds.tsv (identical schema, so plot_round_timings.py
# works unchanged), a timing boxplot, and summary.md. Hub-only metrics (model-status
# timeline, orchestration gap, upload sizes) are marked NOT APPLICABLE.
#
# With --compare <platform rounds.tsv> it also emits a side-by-side steady-state table:
# platform round time minus simulator round time ≈ WAN transfer + platform overhead
# (± hardware differences — see the interpretation caveats written into summary.md).
#
# Timestamps: simulator logs carry local wall-clock times with no timezone
# ("YYYY-MM-DD HH:MM:SS,mmm"). They are parsed as local time; durations are exact,
# absolute times are only as meaningful as the host clock.

set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<EOF
Usage: ${SCRIPT_NAME} [workspace-dir] [options]

Extract FL round metrics from a local NVFLARE simulator workspace
(default workspace: /tmp/nvflare/arkplus_finetuning).

Options:
  -o, --output-dir DIR   Base output directory (default: ./round_metrics)
      --run-name NAME    Output subdirectory name (default: simulator-<workspace basename>-<log mtime>)
      --log FILE         Parse this log file directly (skips workspace discovery)
      --compare TSV      Platform rounds.tsv (from extract_platform_round_timings.sh) to
                         compare against — adds a steady-state overhead table
  -h, --help             Show this help and exit

Examples:
  ${SCRIPT_NAME}                                    # after 'make sim' in the finetuning tutorial
  ${SCRIPT_NAME} /tmp/nvflare/arkplus_finetuning -o round_metrics \\
      --compare round_metrics/eff70d90-5706-4cc9-8087-5059dfb40d96/rounds.tsv
EOF
}

WORKSPACE="/tmp/nvflare/arkplus_finetuning"
OUTPUT_DIR="./round_metrics"
RUN_NAME=""
LOG_FILE=""
COMPARE_TSV=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --run-name)      RUN_NAME="$2"; shift 2 ;;
        --log)           LOG_FILE="$2"; shift 2 ;;
        --compare)       COMPARE_TSV="$2"; shift 2 ;;
        -h|--help)       usage; exit 0 ;;
        -*)              echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
        *)               WORKSPACE="$1"; shift ;;
    esac
done

log()  { echo "[$(date '+%Y-%m-%dT%H:%M:%S')] $*" >&2; }
die()  { echo "[$(date '+%Y-%m-%dT%H:%M:%S')] ERROR: $*" >&2; exit 1; }
warn() { echo "[$(date '+%Y-%m-%dT%H:%M:%S')] WARNING: $*" >&2; }

for dep in date grep sed awk sort; do
    command -v "$dep" >/dev/null 2>&1 || die "Required dependency '$dep' not found on PATH."
done

# --------------------------------------------------------------------------------------
# Locate the log carrying the ScatterAndGather events. The SimEnv layout varies across
# NVFLARE versions (workspace log.txt, simulate_job/log.txt, per-site logs), so among the
# candidates that contain at least one "Round N started." match, pick the most recently
# modified one — NOT the one with the most matches. A retained old full-length experiment
# log (e.g. 50 rounds) would otherwise always beat the 3-round smoke-test log you just ran
# with `make sim`, which is exactly backwards for "what did I just run".
# --------------------------------------------------------------------------------------

if [[ -z "$LOG_FILE" ]]; then
    [[ -d "$WORKSPACE" ]] || die "Workspace directory not found: ${WORKSPACE} (run the simulator first, or pass --log)"
    candidates=()
    while IFS= read -r cand; do
        n="$(grep -cE 'Round [0-9]+ started\.' "$cand" 2>/dev/null || true)"
        [[ "$n" -gt 0 ]] || continue
        mtime="$(date -r "$cand" +%s 2>/dev/null || echo 0)"
        candidates+=("${mtime}"$'\t'"${n}"$'\t'"${cand}")
    done < <(find "$WORKSPACE" -type f \( -name 'log*.txt' -o -name '*.log' \) 2>/dev/null)

    [[ ${#candidates[@]} -gt 0 ]] || die "No log file under ${WORKSPACE} contains 'Round N started.' events."

    best_mtime=-1
    for c in "${candidates[@]}"; do
        IFS=$'\t' read -r c_mtime c_count c_path <<<"$c"
        if [[ "$c_mtime" -gt "$best_mtime" ]]; then
            best_mtime="$c_mtime"
            LOG_FILE="$c_path"
            best_count="$c_count"
        fi
    done

    if [[ ${#candidates[@]} -gt 1 ]]; then
        log "Multiple candidate logs found under ${WORKSPACE}; picking the most recently modified:"
        for c in "${candidates[@]}"; do
            IFS=$'\t' read -r c_mtime c_count c_path <<<"$c"
            marker=" "; [[ "$c_path" == "$LOG_FILE" ]] && marker="*"
            log "  ${marker} ${c_path} (${c_count} round-start events, mtime $(date -d "@${c_mtime}" '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || echo "?"))"
        done
    fi
    log "Using log: ${LOG_FILE} (${best_count} round-start events)"
else
    [[ -f "$LOG_FILE" ]] || die "Log file not found: ${LOG_FILE}"
fi

if [[ -z "$RUN_NAME" ]]; then
    RUN_NAME="simulator-$(basename "$WORKSPACE")-$(date -r "$LOG_FILE" '+%Y%m%dT%H%M%S')"
fi
OUT_DIR="${OUTPUT_DIR%/}/${RUN_NAME}"
mkdir -p "$OUT_DIR"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
log "Output directory: ${OUT_DIR}"

# --------------------------------------------------------------------------------------
# Event extraction. Simulator lines look like:
#   2026-07-10 14:00:01,123 - ScatterAndGather - INFO - [identity=simulator_server, ...] - Round 0 started.
# ts_ms converts "YYYY-MM-DD HH:MM:SS,mmm" to epoch milliseconds (local time).
# --------------------------------------------------------------------------------------

TS_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2},[0-9]{3}'

ts_ms() {
    # "$1" like "2026-07-10 14:00:00,100" — seconds via date(1), millis taken verbatim
    # from the log string (date's %3N truncation is GNU-only and unreliable elsewhere).
    local base="${1%,*}" frac="${1##*,}" s
    s="$(date -d "$base" +%s 2>/dev/null)" || die "Could not parse timestamp: $1"
    echo $(( s * 1000 + 10#$frac ))
}

extract_events() {
    # $1 = pattern (ERE, matched anywhere in line); prints "ms<TAB>line" sorted by ms
    local pattern="$1" out="$2"
    : > "$out"
    while IFS= read -r line; do
        local ts
        ts="$(grep -oE "$TS_RE" <<<"$line" | head -1)"
        [[ -z "$ts" ]] && continue
        # Assign before printf: inlining "$(ts_ms "$ts")" directly as a printf argument
        # would swallow a die() from inside ts_ms — a failed command substitution only
        # trips `set -e` when it's the whole right-hand side of a plain assignment.
        local ms
        ms="$(ts_ms "$ts")"
        printf '%s\t%s\n' "$ms" "$line" >> "$out"
    done < <(grep -E "$pattern" "$LOG_FILE" | sed -E 's/\x1b\[[0-9;]*m//g')
    sort -n -k1,1 -o "$out" "$out"
}

ROUND_STARTS="${WORK_DIR}/round_starts.tsv"   # round \t ms
ROUND_ENDS="${WORK_DIR}/round_ends.tsv"       # round \t ms
AGG_STARTS="${WORK_DIR}/agg_starts.txt"       # ms
AGG_ENDS="${WORK_DIR}/agg_ends.txt"           # ms
CONTRIBS="${WORK_DIR}/contribs.tsv"           # ACCEPTED|REJECTED \t round ('-' if untagged)

extract_events 'Round [0-9]+ started\.' "${WORK_DIR}/rs.raw"
extract_events 'Round [0-9]+ finished\.' "${WORK_DIR}/re.raw"
extract_events 'Start aggregation\.' "${WORK_DIR}/as.raw"
extract_events 'End aggregation\.' "${WORK_DIR}/ae.raw"
# Pinned NVFLARE (2.8.0)'s stock ScatterAndGather unconditionally tags this line with the
# round number ("... by the aggregator at round <N>."); the optional group is a defensive
# fallback for older builds that omitted it, matching extract_platform_round_timings.sh's pattern.
extract_events 'Contribution from [^ ]+ (ACCEPTED|REJECTED) by the aggregator( at round [0-9]+)?\.' "${WORK_DIR}/co.raw"

sed -E 's/^([0-9]+)\t.*Round ([0-9]+) started\..*/\2\t\1/' "${WORK_DIR}/rs.raw" | sort -n -k1,1 > "$ROUND_STARTS"
sed -E 's/^([0-9]+)\t.*Round ([0-9]+) finished\..*/\2\t\1/' "${WORK_DIR}/re.raw" | sort -n -k1,1 > "$ROUND_ENDS"
awk -F'\t' '{print $1}' "${WORK_DIR}/as.raw" > "$AGG_STARTS"
awk -F'\t' '{print $1}' "${WORK_DIR}/ae.raw" > "$AGG_ENDS"
# Columns: verdict \t round ('-' if the line was untagged — pre-round-tag NVFLARE builds).
sed -E 's/^[0-9]+\t.*Contribution from [^ ]+ ([A-Z]+) by the aggregator( at round ([0-9]+))?\..*/\1\t\3/' "${WORK_DIR}/co.raw" \
    | sed -E 's/\t$/\t-/' > "$CONTRIBS"

n_rounds="$(wc -l < "$ROUND_STARTS" | tr -d ' ')"
[[ "$n_rounds" -gt 0 ]] || die "No rounds parsed out of ${LOG_FILE}."
ACCEPTED_TOTAL="$(grep -c $'^ACCEPTED\t' "$CONTRIBS" || true)"
REJECTED_TOTAL="$(grep -c $'^REJECTED\t' "$CONTRIBS" || true)"
log "Parsed ${n_rounds} round(s); contributions: ${ACCEPTED_TOTAL} accepted, ${REJECTED_TOTAL} rejected"

# --------------------------------------------------------------------------------------
# Build rounds.tsv — identical schema to extract_platform_round_timings.sh, so
# plot_round_timings.py consumes it unchanged. Aggregation start/end pairs are matched to
# rounds chronologically (ScatterAndGather aggregates synchronously once per round);
# contributions are matched by their own round tag (see CONTRIBS above) exactly like
# extract_platform_round_timings.sh — untagged ('-') lines only count towards the grand totals above,
# not any specific round.
# NB: "started_utc"/"finished_utc" columns hold the log's LOCAL wall-clock time (the
# simulator writes no timezone); the column names are kept for schema compatibility.
# --------------------------------------------------------------------------------------

ms_to_disp() { date -d "@$(( $1 / 1000 ))" '+%Y-%m-%dT%H:%M:%S'; }
maybe_dur() {
    if [[ -n "${1:-}" && "${1:-}" != "-" && -n "${2:-}" && "${2:-}" != "-" ]]; then
        awk -v a="$1" -v b="$2" 'BEGIN{printf "%.3f", (b - a) / 1000}'
    else
        echo "-"
    fi
}

ROUNDS_TSV="${OUT_DIR}/rounds.tsv"
{
    printf 'round\tstarted_utc\tfinished_utc\tduration_s\tagg_started_utc\tagg_finished_utc\tagg_duration_s\taccepted\trejected\tstart_ms\tend_ms\tagg_start_ms\tagg_end_ms\n'
    i=0
    while IFS=$'\t' read -r round_num start_ms; do
        i=$((i + 1))
        end_ms="$(awk -v r="$round_num" '$1==r{print $2; exit}' "$ROUND_ENDS")"
        agg_start="$(awk -v i="$i" 'NR==i{print}' "$AGG_STARTS")"
        agg_end="$(awk -v i="$i" 'NR==i{print}' "$AGG_ENDS")"
        acc_r="$(awk -F'\t' -v r="$round_num" '$1=="ACCEPTED" && $2==r{n++} END{print n+0}' "$CONTRIBS")"
        rej_r="$(awk -F'\t' -v r="$round_num" '$1=="REJECTED" && $2==r{n++} END{print n+0}' "$CONTRIBS")"
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$round_num" \
            "$(ms_to_disp "$start_ms")" \
            "$([[ -n "$end_ms" ]] && ms_to_disp "$end_ms" || echo "-")" \
            "$(maybe_dur "$start_ms" "${end_ms:--}")" \
            "$([[ -n "$agg_start" ]] && ms_to_disp "$agg_start" || echo "-")" \
            "$([[ -n "$agg_end" ]] && ms_to_disp "$agg_end" || echo "-")" \
            "$(maybe_dur "${agg_start:--}" "${agg_end:--}")" \
            "$acc_r" "$rej_r" \
            "$start_ms" "${end_ms:--}" "${agg_start:--}" "${agg_end:--}"
    done < "$ROUND_STARTS"
} > "$ROUNDS_TSV"
log "Wrote per-round table: ${ROUNDS_TSV}"

# --------------------------------------------------------------------------------------
# Summary statistics (same definitions as the production script; steady-state = rounds >= 1)
# --------------------------------------------------------------------------------------

# col_stats COLUMN [MIN_ROUND] -> "mean \t std \t n" over numeric values of rounds >= MIN_ROUND
col_stats() {
    awk -F'\t' -v c="$1" -v minr="${2:-0}" 'NR > 1 && $1+0 >= minr && $c != "-" {x=$c+0; s+=x; ss+=x*x; n++}
        END { if (n==0) {print "-\t-\t0"; exit}
              m=s/n; sd=(n>1)?sqrt((ss-n*m*m)/(n-1)):0; printf "%.3f\t%.3f\t%d\n", m, sd, n }' "$ROUNDS_TSV"
}
# Inter-round gap = this round's start minus the PREVIOUS round's end. A round with a
# missing start or end breaks that chain, so reset prev_end instead of skipping the row:
# skipping would measure the next round's gap from two rounds back, silently folding the
# whole incomplete round into the gap and inflating the mean.
gap_stats() {
    awk -F'\t' 'NR > 1 {
            if ($10 == "-" || $11 == "-") { prev_end = ""; next }
            if (prev_end != "") {g=($10-prev_end)/1000; s+=g; ss+=g*g; n++}
            prev_end=$11 }
        END { if (n==0) {print "-\t-\t0"; exit}
              m=s/n; sd=(n>1)?sqrt((ss-n*m*m)/(n-1)):0; printf "%.3f\t%.3f\t%d\n", m, sd, n }' "$ROUNDS_TSV"
}

DUR_ALL="$(col_stats 4)";     DUR_SS="$(col_stats 4 1)"
AGG_ALL="$(col_stats 7)";     AGG_SS="$(col_stats 7 1)"
GAP_ALL="$(gap_stats)"
ROUND0_DUR="$(awk -F'\t' 'NR>1 && $1==0 {print $4; exit}' "$ROUNDS_TSV")"
# Span needs BOTH a first start and a last end. An aborted run (no round ever finished)
# leaves max unset, and an unguarded (max-min) then renders as a huge negative span —
# report it as n/a instead. Emitted with its own unit so "n/a" doesn't render as "n/as".
SPAN_S="$(awk -F'\t' '
    NR>1 && $10!="-" {if(!have_min || $10+0<min){min=$10+0; have_min=1}}
    NR>1 && $11!="-" {if(!have_max || $11+0>max){max=$11+0; have_max=1}}
    END{ if (!have_min || !have_max) print "n/a"; else printf "%.0fs", (max-min)/1000 }' "$ROUNDS_TSV")"

# --------------------------------------------------------------------------------------
# Boxplot (best-effort, same runner strategy as the production script)
# --------------------------------------------------------------------------------------

BOXPLOT="${OUT_DIR}/round_timings_boxplot.png"
PLOT_SCRIPT="${SCRIPT_DIR}/plot_round_timings.py"
BOXPLOT_GENERATED=false
if [[ -f "$PLOT_SCRIPT" ]]; then
    plot_cmd=()
    if command -v uv >/dev/null 2>&1; then
        plot_cmd=(uv run --no-project --with pandas --with seaborn python)
    elif command -v python3 >/dev/null 2>&1 && python3 -c 'import pandas, seaborn' >/dev/null 2>&1; then
        plot_cmd=(python3)
    fi
    if [[ ${#plot_cmd[@]} -gt 0 ]]; then
        # --time-label local: simulator logs carry local wall-clock time with no timezone
        # (see the header note), so the plot must not label the start stamp "UTC".
        if "${plot_cmd[@]}" "$PLOT_SCRIPT" "$ROUNDS_TSV" "$BOXPLOT" \
                --model-id "$RUN_NAME" --backend "nvflare-simulator" --time-label local >&2; then
            BOXPLOT_GENERATED=true
            log "Wrote round-timing boxplot: ${BOXPLOT}"
        else
            warn "plot_round_timings.py failed — continuing without the boxplot."
        fi
    else
        warn "Neither uv nor python3-with-pandas+seaborn available — skipping the boxplot."
    fi
fi

# --------------------------------------------------------------------------------------
# Optional platform comparison
# --------------------------------------------------------------------------------------

COMPARISON_MD=""
if [[ -n "$COMPARE_TSV" ]]; then
    [[ -f "$COMPARE_TSV" ]] || die "--compare file not found: ${COMPARE_TSV}"
    # Validate this is actually a rounds.tsv (from either extractor — they share the schema),
    # not an arbitrary file: check the header, not just that a file exists. An unrelated or
    # truncated file would otherwise silently render as a 0-round or garbage comparison.
    EXPECTED_HEADER='round	started_utc	finished_utc	duration_s	agg_started_utc	agg_finished_utc	agg_duration_s	accepted	rejected	start_ms	end_ms	agg_start_ms	agg_end_ms'
    compare_header="$(head -1 "$COMPARE_TSV")"
    [[ "$compare_header" == "$EXPECTED_HEADER" ]] || die "--compare file does not look like a rounds.tsv (unexpected header): ${COMPARE_TSV}"
    [[ "$(wc -l < "$COMPARE_TSV" | tr -d ' ')" -gt 1 ]] || die "--compare file has no data rows: ${COMPARE_TSV}"
    p_stats() {  # same as col_stats but against the platform TSV
        # $c != "-" alone lets a truncated/blank field ("") through too — awk coerces "" to 0
        # and it contributes a phantom 0.0 to the mean instead of being excluded like "-" is.
        awk -F'\t' -v c="$1" -v minr="${2:-0}" 'NR > 1 && $1+0 >= minr && $c != "-" && $c != "" {x=$c+0; s+=x; ss+=x*x; n++}
            END { if (n==0) {print "-\t-\t0"; exit}
                  m=s/n; sd=(n>1)?sqrt((ss-n*m*m)/(n-1)):0; printf "%.3f\t%.3f\t%d\n", m, sd, n }' "$COMPARE_TSV"
    }
    P_DUR_SS="$(p_stats 4 1)"; P_AGG_SS="$(p_stats 7 1)"
    P_ROUND0="$(awk -F'\t' 'NR>1 && $1==0 {print $4; exit}' "$COMPARE_TSV")"
    # Same chain-reset as gap_stats above — see the comment there.
    P_GAP="$(awk -F'\t' 'NR > 1 {
            if ($10 == "-" || $11 == "-") { prev_end = ""; next }
            if (prev_end != "") {g=($10-prev_end)/1000; s+=g; ss+=g*g; n++}
            prev_end=$11 }
        END { if (n==0) {print "-\t-\t0"; exit}
              m=s/n; sd=(n>1)?sqrt((ss-n*m*m)/(n-1)):0; printf "%.3f\t%.3f\t%d\n", m, sd, n }' "$COMPARE_TSV")"

    delta() { awk -v p="$1" -v s="$2" 'BEGIN{ if (p=="-"||s=="-") print "-"; else printf "%+.3f", p-s }'; }
    p_round0_n=0; [[ -n "$P_ROUND0" ]] && p_round0_n=1
    s_round0_n=0; [[ -n "$ROUND0_DUR" ]] && s_round0_n=1
    IFS=$'\t' read -r pd psd pdn <<<"$P_DUR_SS";  IFS=$'\t' read -r sd_ ssd sdn <<<"$DUR_SS"
    IFS=$'\t' read -r pa pasd pan <<<"$P_AGG_SS"; IFS=$'\t' read -r sa sasd san <<<"$AGG_SS"
    IFS=$'\t' read -r pg pgsd pgn <<<"$P_GAP";    IFS=$'\t' read -r sg sgsd sgn <<<"$GAP_ALL"

    COMPARISON_MD=$(cat <<EOF

## Platform vs simulator (steady-state rounds, i.e. round >= 1)

A 3-round smoke run and a 49-round steady-state run render with the same table shape — check
the n columns before reading the deltas as authoritative.

| Metric | Platform (s) | n (platform) | Simulator (s) | n (simulator) | Delta (platform − simulator, s) |
|---|---|---|---|---|---|
| Round duration | ${pd} ± ${psd} | ${pdn} | ${sd_} ± ${ssd} | ${sdn} | $(delta "$pd" "$sd_") |
| Aggregation | ${pa} ± ${pasd} | ${pan} | ${sa} ± ${sasd} | ${san} | $(delta "$pa" "$sa") |
| Inter-round gap | ${pg} ± ${pgsd} | ${pgn} | ${sg} ± ${sgsd} | ${sgn} | $(delta "$pg" "$sg") |
| Round 0 (initialisation) | ${P_ROUND0:--} | ${p_round0_n} | ${ROUND0_DUR:--} | ${s_round0_n} | $(delta "${P_ROUND0:--}" "${ROUND0_DUR:--}") |

Platform source: \`${COMPARE_TSV}\`. The round-duration delta bundles WAN model
transfer, platform transport/orchestration overhead, and any hardware difference
between the production clients and this host — see the caveats below before
attributing it to any single cause.
EOF
    )
fi

# --------------------------------------------------------------------------------------
# summary.md
# --------------------------------------------------------------------------------------

SUMMARY="${OUT_DIR}/summary.md"
fmt_row() { local IFS=$'\t'; read -r m s n <<<"$1"; echo "| $2 | ${m} | ${s} | ${n} |"; }

{
    echo "# FL simulator metrics — ${RUN_NAME}"
    echo
    echo "- **Workspace**: ${WORKSPACE}"
    echo "- **Log parsed**: ${LOG_FILE}"
    echo "- **Generated**: $(date '+%Y-%m-%dT%H:%M:%S')"
    echo "- **Backend**: NVFLARE (local SimEnv simulator; FLIP ScatterAndGather controller)"
    echo
    echo "## Communication rounds"
    echo
    echo "**${n_rounds} round(s) executed**; contributions in-log: **${ACCEPTED_TOTAL} accepted**, **${REJECTED_TOTAL} rejected** (per-round breakdown in the table below matches each contribution to its own \`at round <N>\` tag; untagged lines, from pre-round-tag NVFLARE builds, only count towards these totals)."
    echo
    echo "Total span (first round start to last round end): **${SPAN_S}**."
    echo
    echo "## Timing summary (all rounds / steady-state)"
    echo
    echo "| Metric | Mean (s) | Std (s) | n |"
    echo "|---|---|---|---|"
    fmt_row "$DUR_ALL" "Round duration (all)"
    fmt_row "$DUR_SS"  "Round duration (rounds >= 1)"
    fmt_row "$AGG_ALL" "Aggregation (all)"
    fmt_row "$AGG_SS"  "Aggregation (rounds >= 1)"
    fmt_row "$GAP_ALL" "Inter-round gap"
    echo
    echo "Round 0 duration: **${ROUND0_DUR:-n/a}s** (one-off initialisation: weight load, data-loader start-up, first-access caching)."
    echo
    echo "Machine-readable copy: \`rounds.tsv\` (same schema as extract_platform_round_timings.sh)."
    if [[ "$BOXPLOT_GENERATED" == "true" ]]; then
        echo "Timing distributions: \`round_timings_boxplot.png\`."
    fi
    echo "$COMPARISON_MD"
    echo
    echo "## Not applicable in the simulator"
    echo
    echo "- Model-status timeline / wall-clock from model creation (no Central Hub API)."
    echo "- Orchestration gap around the training attempt (no job dispatch / result upload)."
    echo "- Application upload sizes (no S3 upload; files staged locally)."
    echo
    echo "## Interpretation caveats"
    echo
    echo "1. Both simulated clients run **on this single host, sharing one GPU** (SimEnv"
    echo "   \`num_threads = num_clients\`), so per-round compute reflects GPU contention"
    echo "   between the two clients, not two independent sites."
    echo "2. There is **no WAN, TLS, task encryption, or trust-side polling** — the round"
    echo "   envelope here is essentially pure local compute plus in-process transport."
    echo "3. Same controller and privacy-filter configuration as production"
    echo "   (FlipFedAvgRecipe defaults: percentile=10, gamma=0.01), so server-side"
    echo "   aggregation cost is directly comparable."
    echo "4. Platform-minus-simulator round-duration deltas therefore bundle network"
    echo "   transfer + platform overhead ± hardware differences; use the aggregation and"
    echo "   inter-round-gap rows (host-independent, tiny) as the sanity anchor."
} > "$SUMMARY"

log "Wrote summary: ${SUMMARY}"
log "Done. Output: ${OUT_DIR}"
