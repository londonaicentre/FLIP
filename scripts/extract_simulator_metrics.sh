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
# extract_simulator_metrics.sh — local-simulator counterpart of extract_model_metrics.sh.
#
# Parses an NVFLARE SimEnv workspace's logs for the SAME controller events the production
# script pulls from CloudWatch (flip.nvflare.controllers.scatter_and_gather.ScatterAndGather
# emits identical lines in both settings):
#     "Round N started." / "Round N finished."
#     "Start aggregation." / "End aggregation."
#     "Contribution from <site> ACCEPTED|REJECTED by the aggregator."
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
(default workspace: /tmp/nvflare/finetuning).

Options:
  -o, --output-dir DIR   Base output directory (default: ./model_metrics)
      --run-name NAME    Output subdirectory name (default: simulator-<workspace basename>-<log mtime>)
      --log FILE         Parse this log file directly (skips workspace discovery)
      --compare TSV      Platform rounds.tsv (from extract_model_metrics.sh) to
                         compare against — adds a steady-state overhead table
  -h, --help             Show this help and exit

Examples:
  ${SCRIPT_NAME}                                    # after 'make sim' in the finetuning tutorial
  ${SCRIPT_NAME} /tmp/nvflare/finetuning -o model_metrics \\
      --compare model_metrics/eff70d90-5706-4cc9-8087-5059dfb40d96/rounds.tsv
EOF
}

WORKSPACE="/tmp/nvflare/finetuning"
OUTPUT_DIR="./model_metrics"
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
# NVFLARE versions (workspace log.txt, simulate_job/log.txt, per-site logs), so pick the
# candidate with the most "Round N started." matches instead of hardcoding a path.
# --------------------------------------------------------------------------------------

if [[ -z "$LOG_FILE" ]]; then
    [[ -d "$WORKSPACE" ]] || die "Workspace directory not found: ${WORKSPACE} (run the simulator first, or pass --log)"
    best_count=0
    while IFS= read -r cand; do
        n="$(grep -cE 'Round [0-9]+ started\.' "$cand" 2>/dev/null || true)"
        if [[ "$n" -gt "$best_count" ]]; then
            best_count="$n"
            LOG_FILE="$cand"
        fi
    done < <(find "$WORKSPACE" -type f \( -name 'log*.txt' -o -name '*.log' \) 2>/dev/null)
    [[ -n "$LOG_FILE" ]] || die "No log file under ${WORKSPACE} contains 'Round N started.' events."
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
        printf '%s\t%s\n' "$(ts_ms "$ts")" "$line" >> "$out"
    done < <(grep -E "$pattern" "$LOG_FILE" | sed -E 's/\x1b\[[0-9;]*m//g')
    sort -n -k1,1 -o "$out" "$out"
}

ROUND_STARTS="${WORK_DIR}/round_starts.tsv"   # round \t ms
ROUND_ENDS="${WORK_DIR}/round_ends.tsv"       # round \t ms
AGG_STARTS="${WORK_DIR}/agg_starts.txt"       # ms
AGG_ENDS="${WORK_DIR}/agg_ends.txt"           # ms
CONTRIBS="${WORK_DIR}/contribs.tsv"           # ms \t ACCEPTED|REJECTED

extract_events 'Round [0-9]+ started\.' "${WORK_DIR}/rs.raw"
extract_events 'Round [0-9]+ finished\.' "${WORK_DIR}/re.raw"
extract_events 'Start aggregation\.' "${WORK_DIR}/as.raw"
extract_events 'End aggregation\.' "${WORK_DIR}/ae.raw"
extract_events 'Contribution from [^ ]+ (ACCEPTED|REJECTED) by the aggregator' "${WORK_DIR}/co.raw"

sed -E 's/^([0-9]+)\t.*Round ([0-9]+) started\..*/\2\t\1/' "${WORK_DIR}/rs.raw" | sort -n -k1,1 > "$ROUND_STARTS"
sed -E 's/^([0-9]+)\t.*Round ([0-9]+) finished\..*/\2\t\1/' "${WORK_DIR}/re.raw" | sort -n -k1,1 > "$ROUND_ENDS"
awk -F'\t' '{print $1}' "${WORK_DIR}/as.raw" > "$AGG_STARTS"
awk -F'\t' '{print $1}' "${WORK_DIR}/ae.raw" > "$AGG_ENDS"
sed -E 's/^([0-9]+)\t.*Contribution from [^ ]+ (ACCEPTED|REJECTED) by the aggregator.*/\1\t\2/' "${WORK_DIR}/co.raw" > "$CONTRIBS"

n_rounds="$(wc -l < "$ROUND_STARTS" | tr -d ' ')"
[[ "$n_rounds" -gt 0 ]] || die "No rounds parsed out of ${LOG_FILE}."
ACCEPTED_TOTAL="$(grep -c $'\tACCEPTED' "$CONTRIBS" || true)"
REJECTED_TOTAL="$(grep -c $'\tREJECTED' "$CONTRIBS" || true)"
log "Parsed ${n_rounds} round(s); contributions: ${ACCEPTED_TOTAL} accepted, ${REJECTED_TOTAL} rejected"

# --------------------------------------------------------------------------------------
# Build rounds.tsv — identical schema to extract_model_metrics.sh, so
# plot_round_timings.py consumes it unchanged. Aggregation start/end pairs are matched to
# rounds chronologically (ScatterAndGather aggregates synchronously once per round);
# contributions carry no round tag here, so they are attributed by time window.
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
        if [[ -n "$end_ms" ]]; then
            acc_r="$(awk -F'\t' -v s="$start_ms" -v e="$end_ms" '$2=="ACCEPTED" && ($1+0)>=s && ($1+0)<=e {n++} END{print n+0}' "$CONTRIBS")"
            rej_r="$(awk -F'\t' -v s="$start_ms" -v e="$end_ms" '$2=="REJECTED" && ($1+0)>=s && ($1+0)<=e {n++} END{print n+0}' "$CONTRIBS")"
        else
            acc_r="-"; rej_r="-"
        fi
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
gap_stats() {
    awk -F'\t' 'NR > 1 && $10 != "-" && $11 != "-" {
            if (prev_end != "") {g=($10-prev_end)/1000; s+=g; ss+=g*g; n++}
            prev_end=$11 }
        END { if (n==0) {print "-\t-\t0"; exit}
              m=s/n; sd=(n>1)?sqrt((ss-n*m*m)/(n-1)):0; printf "%.3f\t%.3f\t%d\n", m, sd, n }' "$ROUNDS_TSV"
}

DUR_ALL="$(col_stats 4)";     DUR_SS="$(col_stats 4 1)"
AGG_ALL="$(col_stats 7)";     AGG_SS="$(col_stats 7 1)"
GAP_ALL="$(gap_stats)"
ROUND0_DUR="$(awk -F'\t' 'NR>1 && $1==0 {print $4; exit}' "$ROUNDS_TSV")"
SPAN_S="$(awk -F'\t' 'NR>1 && $10!="-" {if(min==""||$10+0<min)min=$10+0} NR>1 && $11!="-" {if($11+0>max)max=$11+0} END{printf "%.0f", (max-min)/1000}' "$ROUNDS_TSV")"

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
        if "${plot_cmd[@]}" "$PLOT_SCRIPT" "$ROUNDS_TSV" "$BOXPLOT" \
                --model-id "$RUN_NAME" --backend "nvflare-simulator" >&2; then
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
    p_stats() {  # same as col_stats but against the platform TSV
        awk -F'\t' -v c="$1" -v minr="${2:-0}" 'NR > 1 && $1+0 >= minr && $c != "-" {x=$c+0; s+=x; ss+=x*x; n++}
            END { if (n==0) {print "-\t-\t0"; exit}
                  m=s/n; sd=(n>1)?sqrt((ss-n*m*m)/(n-1)):0; printf "%.3f\t%.3f\t%d\n", m, sd, n }' "$COMPARE_TSV"
    }
    P_DUR_SS="$(p_stats 4 1)"; P_AGG_SS="$(p_stats 7 1)"
    P_ROUND0="$(awk -F'\t' 'NR>1 && $1==0 {print $4; exit}' "$COMPARE_TSV")"
    P_GAP="$(awk -F'\t' 'NR > 1 && $10 != "-" && $11 != "-" {
            if (prev_end != "") {g=($10-prev_end)/1000; s+=g; ss+=g*g; n++}
            prev_end=$11 }
        END { if (n==0) {print "-\t-\t0"; exit}
              m=s/n; sd=(n>1)?sqrt((ss-n*m*m)/(n-1)):0; printf "%.3f\t%.3f\t%d\n", m, sd, n }' "$COMPARE_TSV")"

    delta() { awk -v p="$1" -v s="$2" 'BEGIN{ if (p=="-"||s=="-") print "-"; else printf "%+.3f", p-s }'; }
    IFS=$'\t' read -r pd psd _ <<<"$P_DUR_SS";  IFS=$'\t' read -r sd_ ssd _ <<<"$DUR_SS"
    IFS=$'\t' read -r pa pasd _ <<<"$P_AGG_SS"; IFS=$'\t' read -r sa sasd _ <<<"$AGG_SS"
    IFS=$'\t' read -r pg pgsd _ <<<"$P_GAP";    IFS=$'\t' read -r sg sgsd _ <<<"$GAP_ALL"

    COMPARISON_MD=$(cat <<EOF

## Platform vs simulator (steady-state rounds, i.e. round >= 1)

| Metric | Platform (s) | Simulator (s) | Delta (platform − simulator, s) |
|---|---|---|---|
| Round duration | ${pd} ± ${psd} | ${sd_} ± ${ssd} | $(delta "$pd" "$sd_") |
| Aggregation | ${pa} ± ${pasd} | ${sa} ± ${sasd} | $(delta "$pa" "$sa") |
| Inter-round gap | ${pg} ± ${pgsd} | ${sg} ± ${sgsd} | $(delta "$pg" "$sg") |
| Round 0 (initialisation) | ${P_ROUND0:--} | ${ROUND0_DUR:--} | $(delta "${P_ROUND0:--}" "${ROUND0_DUR:--}") |

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
    echo "**${n_rounds} round(s) executed**; contributions in-log: **${ACCEPTED_TOTAL} accepted**, **${REJECTED_TOTAL} rejected** (attributed to rounds by time window — simulator contribution lines carry no round tag)."
    echo
    echo "Total span (first round start to last round end): **${SPAN_S}s**."
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
    echo "Machine-readable copy: \`rounds.tsv\` (same schema as extract_model_metrics.sh)."
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
    echo "   (FlipFedAvgRecipe defaults: percentile=95, gamma=2.0), so server-side"
    echo "   aggregation cost is directly comparable."
    echo "4. Platform-minus-simulator round-duration deltas therefore bundle network"
    echo "   transfer + platform overhead ± hardware differences; use the aggregation and"
    echo "   inter-round-gap rows (host-independent, tiny) as the sanity anchor."
} > "$SUMMARY"

log "Wrote summary: ${SUMMARY}"
log "Done. Output: ${OUT_DIR}"
