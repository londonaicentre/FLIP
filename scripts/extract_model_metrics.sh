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
#
# extract_model_metrics.sh — pull FL-run metrics for one model out of the Central Hub's
# production CloudWatch logs (flip-api, fl-api-net-*, fl-server-net-*). Trust-side logs
# (EC2, not CloudWatch) are out of scope — see the "NOT AVAILABLE" notes this script writes
# into summary.md for anything that can only be observed there.
#
# ---------------------------------------------------------------------------------------
# WHY THIS SCRIPT LOOKS THE WAY IT DOES (read this before extending it)
# ---------------------------------------------------------------------------------------
#
# 1. Log group names are static Terraform literals, not derivable from model/project IDs:
#      /ecs/flip-api          (deploy/providers/AWS/ecs.tf)
#      /ecs/fl-api-net-1      (one per FL network; today only net-1 exists)
#      /ecs/fl-server-net-1   (one per FL network; today only net-1 exists)
#    A future second network would add /ecs/fl-api-net-2 / /ecs/fl-server-net-2 by the same
#    literal-string convention (there is no Terraform for_each to generalise from yet — see
#    deploy/providers/AWS/ecs.tf). This script auto-discovers whatever exists via
#    `describe-log-groups --log-group-name-prefix`, so it does not need updating when a
#    second net is added.
#
# 2. flip-api's own log lines carry the model_id (it's an internal-service-authenticated
#    endpoint per model — see flip-api/src/flip_api/private_services/), so we can filter
#    flip-api logs directly by the model_id substring. fl-api-net-*/fl-server-net-* logs do
#    NOT mention model_id or project_id anywhere (confirmed by reading
#    flip-utils/flip/nvflare/controllers/scatter_and_gather.py and the fl-api app.py files in
#    both backends) — a net trains one job at a time (the scheduler marks it BUSY), so this
#    script correlates fl-api/fl-server events to the model purely by TIME WINDOW: it first
#    finds the model's TRAINING_STARTED -> terminal-status window from flip-api logs, pads
#    it, and treats everything in that window on the relevant net as belonging to this run.
#    If two nets are ever trained concurrently for genuinely different jobs, an operator
#    would need to add a per-net filter here (e.g. restrict to a specific net's log group
#    once the net used by this model is known some other way) — there is no way to derive
#    that from the hub logs alone today.
#
# 3. IMPORTANT / non-obvious: flip-api's uvicorn-formatted log lines get hard-wrapped at
#    ~80 columns by whatever writes flip-api's stdout, and each wrapped fragment lands as
#    its OWN CloudWatch log event (same timestamp, same log stream, no level prefix on the
#    continuation fragments). fl-api-net-*/fl-server-net-* logs do NOT exhibit this — they
#    come through as single, unwrapped events. Verified live against prod on 2026-07-06,
#    e.g. a single logical line arrived as three raw events:
#        "      INFO   Attempting to set the model's status... ID:"
#        "             846f2bc8-fc82-472a-a453-f6851d5299b3, Status: ModelStatus.INITIATED"
#    Any pattern match against a single raw flip-api event will miss content that landed on
#    a wrapped continuation. This script re-joins wrapped fragments back into logical lines
#    before doing any parsing (see reconstruct_flip_api_lines()) — do not remove that step,
#    and do not try to `grep` the raw flip-api.jsonl dump directly for anything that might
#    span a line wrap (the model_id itself is short enough it is never split, which is what
#    makes the initial coarse discovery filter safe to run against raw events).
#
# 4. Logging is plain Python `logging` via uvicorn's own logger (`logging.getLogger
#    ("uvicorn")`) — NOT structlog, NOT JSON. A few call sites log a raw Python dict
#    (e.g. model_service.add_log()), which becomes a Python *repr* string
#    ({'message': ..., 'modelId': UUID('...'), 'log': '...'}), not JSON — don't try to
#    `jq`-parse message bodies as JSON; everything here is matched with grep/sed regexes
#    against plain text.
#
# 5. CloudWatch Logs Insights was considered (the task brief suggested it for time-window /
#    aggregation queries) but was deliberately NOT used here: Insights' @timestamp field is
#    a formatted string ("YYYY-MM-DD HH:MM:SS.mmm") that has to be re-parsed back into epoch
#    milliseconds, which is one more fragile step on top of the line-wrap issue above with no
#    real benefit at this log volume (a single FL run's logs comfortably fit in a handful of
#    filter-log-events pages). `aws logs filter-log-events` gives native epoch-ms timestamps
#    and was verified working end-to-end against prod. If log volume grows enough that
#    filter-log-events pagination becomes slow, switching find_model_window() and the raw
#    dump loop over to start-query/get-query-results is the natural next step.
#
# Metrics coverage (see summary.md's "Data provenance" table for the authoritative version):
#   1. Communication rounds       — fl-server-net-* logs (NVFLARE: "Round N started/finished";
#                                    Flower: "(round N)" in forwarded-metric lines). FULL for
#                                    NVFLARE, PARTIAL for Flower (round numbers only, no
#                                    explicit round-boundary log line), NONE if backend can't
#                                    be identified from the logs in-window.
#   2. Update size (bytes)        — NOT AVAILABLE. No code path logs the byte size of a
#                                    per-round model-weight update on the hub side (checked
#                                    fl-api upload.py in both backends and flip-utils). Only
#                                    the *initial* model-file bundle's S3 ContentLength is
#                                    logged by flip-api (file_services/uploaded_file.py) —
#                                    reported separately, clearly marked as NOT the same
#                                    metric. Per-round weight size would need trust-side or
#                                    fl-server-side instrumentation that doesn't exist yet.
#   3. Aggregation time            — fl-server-net-* logs, NVFLARE only ("Start aggregation."/
#                                    "End aggregation." pairs). NOT AVAILABLE for Flower (no
#                                    equivalent log line in the platform code).
#   4. Per-round communication time — derived from the same NVFLARE round/aggregation/
#                                    contribution log lines; PARTIAL for Flower (only a rough
#                                    per-round timestamp spread from forwarded metrics).
#   5. Wall-clock time             — flip-api model-status-transition timeline (FULL).
#   6. Deployment failures         — flip-api + fl-api-net-* + fl-server-net-* ERROR/WARNING
#                                    lines and known failure phrases (FULL, best-effort).
#   7. Infra constraints           — generic OOM/timeout/throttle/connection-refused greps
#                                    across all three log groups, plus a best-effort (and
#                                    time-limited — ECS only retains ~1h of stopped-task
#                                    metadata) ECS stopped-task check.
#
# ---------------------------------------------------------------------------------------

set -euo pipefail

# --------------------------------------------------------------------------------------
# Usage / defaults
# --------------------------------------------------------------------------------------

SCRIPT_NAME="$(basename "$0")"

usage() {
    cat <<EOF
Usage: ${SCRIPT_NAME} <flip-ui-model-url> [options]

Extract FL run metrics for one model from production CloudWatch logs
(flip-api, fl-api-net-*, fl-server-net-*).

Arguments:
  <flip-ui-model-url>   e.g. https://app.flip.aicentre.co.uk/project/<project_id>/model/<model_id>

Options:
  -o, --output-dir DIR      Base output directory (default: ./model_metrics)
      --profile PROFILE     AWS CLI profile (default: prod)
      --region REGION       AWS region (default: eu-west-2)
      --start ISO8601       Start of the search window (skips auto-discovery), e.g. 2026-06-30T09:00:00Z
      --end ISO8601         End of the search window (requires --start)
      --search-days N       When --start/--end are not given, how many days back to search
                             for the model_id in flip-api logs (default: 7, matching the
                             CloudWatch retention configured in deploy/providers/AWS/ecs.tf)
      --pad-minutes N        Padding (minutes) applied around the discovered training window
                             before querying fl-api-net-*/fl-server-net-* (default: 10)
      --ecs-cluster NAME     ECS cluster name for the best-effort stopped-task check
                             (default: flip-cluster)
      --skip-ecs-check       Skip the best-effort ECS stopped-task lookup
  -h, --help                 Show this help and exit

Examples:
  ${SCRIPT_NAME} https://app.flip.aicentre.co.uk/project/f48850b7-3101-46d1-8fa7-a00bf01d2597/model/d7d65959-3e40-4744-b853-0ece208df840
  ${SCRIPT_NAME} <url> -o /tmp/metrics --pad-minutes 20
  ${SCRIPT_NAME} <url> --start 2026-06-30T09:00:00Z --end 2026-06-30T11:00:00Z
EOF
}

OUTPUT_DIR="./model_metrics"
AWS_PROFILE_ARG="prod"
AWS_REGION_ARG="eu-west-2"
USER_START=""
USER_END=""
SEARCH_DAYS=7
PAD_MINUTES=10
ECS_CLUSTER="flip-cluster"
SKIP_ECS_CHECK=false
URL=""

# --------------------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--output-dir)
            OUTPUT_DIR="$2"; shift 2 ;;
        --profile)
            AWS_PROFILE_ARG="$2"; shift 2 ;;
        --region)
            AWS_REGION_ARG="$2"; shift 2 ;;
        --start)
            USER_START="$2"; shift 2 ;;
        --end)
            USER_END="$2"; shift 2 ;;
        --search-days)
            SEARCH_DAYS="$2"; shift 2 ;;
        --pad-minutes)
            PAD_MINUTES="$2"; shift 2 ;;
        --ecs-cluster)
            ECS_CLUSTER="$2"; shift 2 ;;
        --skip-ecs-check)
            SKIP_ECS_CHECK=true; shift ;;
        -h|--help)
            usage; exit 0 ;;
        -*)
            echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
        *)
            if [[ -z "$URL" ]]; then
                URL="$1"; shift
            else
                echo "Unexpected extra argument: $1" >&2; usage >&2; exit 1
            fi
            ;;
    esac
done

if [[ -z "$URL" ]]; then
    echo "Error: missing <flip-ui-model-url>" >&2
    usage >&2
    exit 1
fi

if [[ -n "$USER_START" && -z "$USER_END" ]] || [[ -z "$USER_START" && -n "$USER_END" ]]; then
    echo "Error: --start and --end must be given together" >&2
    exit 1
fi

# --------------------------------------------------------------------------------------
# Logging helpers
# --------------------------------------------------------------------------------------

log()  { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" >&2; }
warn() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] WARNING: $*" >&2; }
die()  { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] ERROR: $*" >&2; exit 1; }

# --------------------------------------------------------------------------------------
# Dependency checks
# --------------------------------------------------------------------------------------

for dep in aws jq date grep sed awk; do
    command -v "$dep" >/dev/null 2>&1 || die "Required dependency '$dep' not found on PATH."
done

# --------------------------------------------------------------------------------------
# Parse & validate the FLIP UI URL
# --------------------------------------------------------------------------------------

UUID_RE='[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'

if [[ "$URL" =~ /project/(${UUID_RE})/model/(${UUID_RE}) ]]; then
    PROJECT_ID="${BASH_REMATCH[1],,}"
    MODEL_ID="${BASH_REMATCH[2],,}"
else
    die "Could not parse a /project/<uuid>/model/<uuid> path out of: ${URL}"
fi

log "Parsed project_id=${PROJECT_ID} model_id=${MODEL_ID}"

# --------------------------------------------------------------------------------------
# AWS CLI wrapper + auth check
# --------------------------------------------------------------------------------------

awscli() {
    aws --profile "$AWS_PROFILE_ARG" --region "$AWS_REGION_ARG" --output json "$@"
}

if ! awscli sts get-caller-identity >/dev/null 2>&1; then
    die "Could not authenticate with AWS profile '${AWS_PROFILE_ARG}'. Run 'aws sso login --profile ${AWS_PROFILE_ARG}' and retry. (Per CLAUDE.md, production access uses AWS SSO under profile 'prod'.)"
fi

log "Authenticated as: $(awscli sts get-caller-identity --query Arn --output text)"

# --------------------------------------------------------------------------------------
# Output directories
# --------------------------------------------------------------------------------------

MODEL_OUT_DIR="${OUTPUT_DIR%/}/${MODEL_ID}"
LOGS_DIR="${MODEL_OUT_DIR}/logs"
mkdir -p "$LOGS_DIR"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

log "Output directory: ${MODEL_OUT_DIR}"

# --------------------------------------------------------------------------------------
# Resolve / discover log groups
# --------------------------------------------------------------------------------------

FLIP_API_LOG_GROUP="/ecs/flip-api"

log_group_exists() {
    local name="$1"
    awscli logs describe-log-groups --log-group-name-prefix "$name" \
        --query "logGroups[?logGroupName=='${name}'] | length(@)" --output text 2>/dev/null | grep -qv '^0$'
}

log_group_exists "$FLIP_API_LOG_GROUP" || die "Log group ${FLIP_API_LOG_GROUP} not found — check --profile/--region."

discover_log_groups() {
    # Prints one log group name per line matching the given prefix.
    local prefix="$1"
    awscli logs describe-log-groups --log-group-name-prefix "$prefix" \
        --query 'logGroups[].logGroupName' --output text 2>/dev/null | tr '\t' '\n' | sed '/^$/d'
}

mapfile -t FL_API_LOG_GROUPS < <(discover_log_groups "/ecs/fl-api-net-")
mapfile -t FL_SERVER_LOG_GROUPS < <(discover_log_groups "/ecs/fl-server-net-")

if [[ ${#FL_API_LOG_GROUPS[@]} -eq 0 ]]; then
    warn "No /ecs/fl-api-net-* log groups discovered — fl-api-side metrics will be unavailable."
fi
if [[ ${#FL_SERVER_LOG_GROUPS[@]} -eq 0 ]]; then
    warn "No /ecs/fl-server-net-* log groups discovered — round/aggregation metrics will be unavailable."
fi

log "Log groups: ${FLIP_API_LOG_GROUP}, fl-api=[${FL_API_LOG_GROUPS[*]:-none}], fl-server=[${FL_SERVER_LOG_GROUPS[*]:-none}]"

# --------------------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------------------

now_ms() { echo $(( $(date -u +%s) * 1000 )); }

iso_to_ms() {
    local iso="$1"
    local s
    s="$(date -u -d "$iso" +%s 2>/dev/null)" || die "Could not parse timestamp: ${iso}"
    echo $(( s * 1000 ))
}

ms_to_iso() {
    local ms="$1"
    date -u -d "@$(( ms / 1000 ))" '+%Y-%m-%dT%H:%M:%SZ'
}

# --------------------------------------------------------------------------------------
# fetch_log_events LOG_GROUP START_MS END_MS FILTER_PATTERN OUT_FILE
#
# Paginates `aws logs filter-log-events` across [START_MS, END_MS) and writes one compact
# JSON object per line ({timestamp, logStreamName, message}) to OUT_FILE, sorted by
# timestamp. FILTER_PATTERN may be an empty string for "no filter" (dump everything).
# Capped at MAX_PAGES pages as a safety net against runaway pagination on a mis-set window.
# --------------------------------------------------------------------------------------

MAX_PAGES=500

fetch_log_events() {
    local log_group="$1" start_ms="$2" end_ms="$3" filter_pattern="$4" out_file="$5"
    local raw_file="${WORK_DIR}/$(basename "$out_file").raw"
    : > "$raw_file"

    local next_token="" page=0
    while :; do
        page=$((page + 1))
        if [[ $page -gt $MAX_PAGES ]]; then
            warn "fetch_log_events: hit MAX_PAGES=${MAX_PAGES} for ${log_group}; results may be truncated. Narrow the window with --start/--end/--pad-minutes."
            break
        fi

        local args=(logs filter-log-events --log-group-name "$log_group" \
            --start-time "$start_ms" --end-time "$end_ms" --interleaved)
        [[ -n "$filter_pattern" ]] && args+=(--filter-pattern "$filter_pattern")
        [[ -n "$next_token" ]] && args+=(--next-token "$next_token")

        local resp
        if ! resp="$(awscli "${args[@]}" 2>&1)"; then
            warn "fetch_log_events failed for ${log_group} (page ${page}): ${resp}"
            break
        fi

        jq -c '.events[] | {timestamp, logStreamName, message}' <<<"$resp" >> "$raw_file"

        next_token="$(jq -r '.nextToken // empty' <<<"$resp")"
        [[ -z "$next_token" ]] && break
    done

    jq -c -s 'sort_by(.timestamp) | .[]' "$raw_file" > "$out_file" 2>/dev/null || : > "$out_file"
    wc -l < "$out_file" | tr -d ' '
}

# --------------------------------------------------------------------------------------
# reconstruct_flip_api_lines IN_FILE OUT_FILE
#
# flip-api's stdout is hard-wrapped at ~80 columns before it reaches CloudWatch (see the
# header comment, point 3). Each wrapped fragment is its own log event with no level
# prefix. This rejoins fragments into logical lines within each log stream (continuation =
# any event whose message does NOT start with a log-level keyword), and collapses the
# incidental double-spacing the wrap leaves behind.
# --------------------------------------------------------------------------------------

RECONSTRUCT_JQ="${WORK_DIR}/reconstruct.jq"
cat > "$RECONSTRUCT_JQ" <<'JQ_EOF'
[inputs]
| group_by(.logStreamName)
| map(
    reduce .[] as $e (
      {lines: [], cur: null};
      if ($e.message | test("^[ \t]*(DEBUG|INFO|WARNING|ERROR|CRITICAL)[ \t]")) then
        {lines: (.lines + (if .cur then [.cur] else [] end)),
         cur: {timestamp: $e.timestamp, logStreamName: $e.logStreamName,
               message: ($e.message | sub("^[ \t]+"; ""))}}
      else
        (.cur // {timestamp: $e.timestamp, logStreamName: $e.logStreamName, message: ""}) as $base
        | {lines: .lines,
           cur: ($base
                 | .message |= (. + " " + ($e.message | sub("^[ \t]+"; "") | sub("[ \t]+$"; "")))
                 | .message |= sub("^ "; ""))}
      end
    )
    | .lines + (if .cur then [.cur] else [] end)
  )
| flatten
| map(.message |= gsub("[ \t]+"; " "))
| sort_by(.timestamp)
| .[]
JQ_EOF

reconstruct_flip_api_lines() {
    local in_file="$1" out_file="$2"
    jq -c -f "$RECONSTRUCT_JQ" "$in_file" < /dev/null > "$out_file"
}

# --------------------------------------------------------------------------------------
# Step 1: coarse discovery — find the model's activity window in flip-api logs
# --------------------------------------------------------------------------------------

if [[ -n "$USER_START" ]]; then
    SEARCH_START_MS="$(iso_to_ms "$USER_START")"
    SEARCH_END_MS="$(iso_to_ms "$USER_END")"
    log "Using user-supplied window: ${USER_START} to ${USER_END}"
else
    SEARCH_END_MS="$(now_ms)"
    SEARCH_START_MS=$(( SEARCH_END_MS - SEARCH_DAYS * 24 * 60 * 60 * 1000 ))
    log "Searching flip-api logs for model_id over the last ${SEARCH_DAYS} day(s) (retention is 7 days per deploy/providers/AWS/ecs.tf)"
fi

COARSE_HITS_FILE="${WORK_DIR}/flip_api_coarse_hits.jsonl"
n_hits="$(fetch_log_events "$FLIP_API_LOG_GROUP" "$SEARCH_START_MS" "$SEARCH_END_MS" "\"${MODEL_ID}\"" "$COARSE_HITS_FILE")"

if [[ "$n_hits" -eq 0 ]]; then
    die "No flip-api log events mention model_id ${MODEL_ID} in the searched window. Either the model is older than the 7-day CloudWatch retention, it never started, or --start/--end need to be widened."
fi

log "Found ${n_hits} raw flip-api log events mentioning the model_id (coarse, pre-wrap-reassembly)"

ROUGH_START_MS="$(jq -s 'map(.timestamp) | min' "$COARSE_HITS_FILE")"
ROUGH_END_MS="$(jq -s 'map(.timestamp) | max' "$COARSE_HITS_FILE")"
PAD_MS=$(( PAD_MINUTES * 60 * 1000 ))
DISCOVERY_START_MS=$(( ROUGH_START_MS - PAD_MS ))
DISCOVERY_END_MS=$(( ROUGH_END_MS + PAD_MS ))

log "Rough model activity window: $(ms_to_iso "$ROUGH_START_MS") to $(ms_to_iso "$ROUGH_END_MS") (flip-api)"

# --------------------------------------------------------------------------------------
# Step 2: full flip-api dump over the padded window, reconstructed, then filtered to
# lines that actually mention the model_id — this is the authoritative per-model timeline.
# --------------------------------------------------------------------------------------

FLIP_API_RAW="${LOGS_DIR}/flip-api.jsonl"
n_all="$(fetch_log_events "$FLIP_API_LOG_GROUP" "$DISCOVERY_START_MS" "$DISCOVERY_END_MS" "" "$FLIP_API_RAW")"
log "Dumped ${n_all} raw flip-api events in the padded window to ${FLIP_API_RAW}"

FLIP_API_RECONSTRUCTED="${LOGS_DIR}/flip-api.reconstructed.log"
reconstruct_flip_api_lines "$FLIP_API_RAW" "${WORK_DIR}/flip_api_reconstructed.jsonl"
jq -r '"\(.timestamp | tostring) \(.logStreamName) \(.message)"' "${WORK_DIR}/flip_api_reconstructed.jsonl" > "$FLIP_API_RECONSTRUCTED"
log "Reassembled wrapped flip-api lines -> ${FLIP_API_RECONSTRUCTED} (see header comment #3 for why this step exists)"

MODEL_LINES_FILE="${LOGS_DIR}/flip-api.model-lines.log"
grep -F "$MODEL_ID" "$FLIP_API_RECONSTRUCTED" > "$MODEL_LINES_FILE" || true
log "Extracted $(wc -l < "$MODEL_LINES_FILE" | tr -d ' ') reconstructed lines mentioning the model -> ${MODEL_LINES_FILE}"

if [[ ! -s "$MODEL_LINES_FILE" ]]; then
    die "Reconstructed flip-api lines contain no mention of ${MODEL_ID} — this should not happen given the coarse hits above; please inspect ${FLIP_API_RECONSTRUCTED} manually."
fi

# Single-pass min/max in awk: `sort | head -1` under pipefail dies with SIGPIPE (141)
# once the input outgrows the pipe buffer, because head exits after one line.
TRUE_START_MS="$(awk 'NR==1||$1+0<min{min=$1+0} END{print min}' "$MODEL_LINES_FILE")"
TRUE_END_MS="$(awk 'NR==1||$1+0>max{max=$1+0} END{print max}' "$MODEL_LINES_FILE")"

# --------------------------------------------------------------------------------------
# Step 3: status-transition timeline (metric #5: wall-clock time, phase breakdown)
#
# Primary source: "Attempting to set the model's status... ID: <model_id>, Status: <TOKEN>"
# (flip_api/model_services/services/model_service.py:91). We deliberately do NOT also use
# the paired "The status of Model ID: ... has been updated to <TOKEN>" confirmation line as
# an independent source — it fires even on no-op refresh calls that pass status=None (which
# resolve to the model's *current*, unchanged status), so using it standalone would fabricate
# duplicate transitions. Consecutive duplicate tokens from the primary source are also
# deduped defensively below.
# --------------------------------------------------------------------------------------

STATUS_TIMELINE_FILE="${WORK_DIR}/status_timeline.tsv"
grep -F "Attempting to set the model's status" "$MODEL_LINES_FILE" \
    | grep -oP "^\S+ \S+ .*ID: ${MODEL_ID}, Status: \K.*" \
    > "${WORK_DIR}/status_raw.txt" || true

# Re-pair timestamps (first field of the matching line) with the extracted status token.
: > "$STATUS_TIMELINE_FILE"
while IFS= read -r line; do
    ts="$(awk '{print $1}' <<<"$line")"
    token="$(grep -oP "Status: \K\S+" <<<"$line" || true)"
    [[ "$token" == ModelStatus.* ]] && printf '%s\t%s\n' "$ts" "$token" >> "$STATUS_TIMELINE_FILE"
done < <(grep -F "Attempting to set the model's status" "$MODEL_LINES_FILE")

# Dedupe consecutive identical statuses, keep chronological order.
STATUS_TIMELINE_DEDUP="${WORK_DIR}/status_timeline_dedup.tsv"
sort -n -k1,1 "$STATUS_TIMELINE_FILE" \
    | awk -F'\t' '$2 != prev {print; prev=$2}' \
    > "$STATUS_TIMELINE_DEDUP"

log "Status transitions found: $(wc -l < "$STATUS_TIMELINE_DEDUP" | tr -d ' ')"

# --------------------------------------------------------------------------------------
# Step 4: identify TRAINING_STARTED -> terminal-status "attempts" (handles retries: a model
# can be started, error out, and be requeued/restarted).
# --------------------------------------------------------------------------------------

TERMINAL_STATUSES_RE='ModelStatus\.(ERROR|STOPPED|RESULTS_UPLOADED|RESULTS_UPLOAD_FAILED)$'
ATTEMPTS_FILE="${WORK_DIR}/attempts.tsv"   # start_ms \t end_ms \t terminal_status (end_ms may be empty = still running/unknown)
: > "$ATTEMPTS_FILE"

pending_start=""
while IFS=$'\t' read -r ts status; do
    if [[ "$status" == "ModelStatus.TRAINING_STARTED" ]]; then
        pending_start="$ts"
    elif [[ -n "$pending_start" ]] && [[ "$status" =~ $TERMINAL_STATUSES_RE ]]; then
        printf '%s\t%s\t%s\n' "$pending_start" "$ts" "$status" >> "$ATTEMPTS_FILE"
        pending_start=""
    fi
done < "$STATUS_TIMELINE_DEDUP"

if [[ -n "$pending_start" ]]; then
    # Training started but no terminal status seen in-window (still running, or the window
    # cut it off) — use the last known model-mention timestamp as a provisional end.
    printf '%s\t%s\t%s\n' "$pending_start" "$TRUE_END_MS" "UNKNOWN (still training or window truncated)" >> "$ATTEMPTS_FILE"
fi

n_attempts="$(wc -l < "$ATTEMPTS_FILE" | tr -d ' ')"
log "Identified ${n_attempts} training attempt(s) for this model"

# --------------------------------------------------------------------------------------
# Step 5: dump fl-api-net-*/fl-server-net-* logs across the union of all attempt windows
# (padded). These logs carry no model_id/project_id — see header comment #2 for why time
# correlation is the best available signal.
# --------------------------------------------------------------------------------------

FL_SPAN_START_MS="$TRUE_START_MS"
FL_SPAN_END_MS="$TRUE_END_MS"
if [[ -s "$ATTEMPTS_FILE" ]]; then
    FL_SPAN_START_MS="$(awk -F'\t' 'NR==1||$1+0<min{min=$1+0} END{print min}' "$ATTEMPTS_FILE")"
    FL_SPAN_END_MS="$(awk -F'\t' 'NR==1||$2+0>max{max=$2+0} END{print max}' "$ATTEMPTS_FILE")"
fi
FL_SPAN_START_MS=$(( FL_SPAN_START_MS - PAD_MS ))
FL_SPAN_END_MS=$(( FL_SPAN_END_MS + PAD_MS ))

log "Querying fl-api-net-*/fl-server-net-* over $(ms_to_iso "$FL_SPAN_START_MS") to $(ms_to_iso "$FL_SPAN_END_MS") (padded ${PAD_MINUTES}m)"

FL_SERVER_DUMPS=()
for grp in "${FL_SERVER_LOG_GROUPS[@]:-}"; do
    [[ -z "$grp" ]] && continue
    base="$(basename "$grp")"
    out="${LOGS_DIR}/${base}.jsonl"
    n="$(fetch_log_events "$grp" "$FL_SPAN_START_MS" "$FL_SPAN_END_MS" "" "$out")"
    log "Dumped ${n} events from ${grp} -> ${out}"
    FL_SERVER_DUMPS+=("$out")
done

FL_API_DUMPS=()
for grp in "${FL_API_LOG_GROUPS[@]:-}"; do
    [[ -z "$grp" ]] && continue
    base="$(basename "$grp")"
    out="${LOGS_DIR}/${base}.jsonl"
    n="$(fetch_log_events "$grp" "$FL_SPAN_START_MS" "$FL_SPAN_END_MS" "" "$out")"
    log "Dumped ${n} events from ${grp} -> ${out}"
    FL_API_DUMPS+=("$out")
done

# Flattened, ANSI-stripped, plain-text views used for local parsing (raw .jsonl files above
# are left untouched for reproducibility).
strip_ansi_and_flatten() {
    # $1 = jsonl file (may not exist / be empty) -> prints "timestamp message" lines
    local f="$1"
    [[ -s "$f" ]] || return 0
    jq -r '"\(.timestamp) \(.message)"' "$f" | sed -E 's/\x1b\[[0-9;]*m//g'
}

FL_SERVER_TEXT="${WORK_DIR}/fl_server_all.txt"
: > "$FL_SERVER_TEXT"
for f in "${FL_SERVER_DUMPS[@]:-}"; do strip_ansi_and_flatten "$f" >> "$FL_SERVER_TEXT"; done
sort -n -k1,1 -o "$FL_SERVER_TEXT" "$FL_SERVER_TEXT" 2>/dev/null || true

FL_API_TEXT="${WORK_DIR}/fl_api_all.txt"
: > "$FL_API_TEXT"
for f in "${FL_API_DUMPS[@]:-}"; do strip_ansi_and_flatten "$f" >> "$FL_API_TEXT"; done
sort -n -k1,1 -o "$FL_API_TEXT" "$FL_API_TEXT" 2>/dev/null || true

# --------------------------------------------------------------------------------------
# Step 6: detect FL backend from the log content actually observed in-window.
#
# NVFLARE's ScatterAndGather controller (flip-utils/flip/nvflare/controllers/
# scatter_and_gather.py) logs "Round N started."/"Round N finished." and
# "Start aggregation."/"End aggregation.". Flower has no equivalent in-repo server code
# (stock flwr framework); the only round-tagged signal is flip-utils/flip/flower/metrics.py
# forwarding "... (round N)" for each client metric.
# --------------------------------------------------------------------------------------

BACKEND="unknown"
if grep -qP 'Round \d+ (started|finished)\.' "$FL_SERVER_TEXT" 2>/dev/null; then
    BACKEND="nvflare"
elif grep -qP '\(round \d+\)' "$FL_SERVER_TEXT" "$FL_API_TEXT" 2>/dev/null; then
    BACKEND="flower"
fi
log "Detected FL backend from logs: ${BACKEND}"

# --------------------------------------------------------------------------------------
# Step 7: metrics #1/#3/#4 — communication rounds, aggregation time, per-round timing
# --------------------------------------------------------------------------------------

ROUND_TABLE_FILE="${WORK_DIR}/round_table.tsv"
: > "$ROUND_TABLE_FILE"
# Columns: round  started_ms  finished_ms  agg_start_ms  agg_end_ms  accepted  rejected

if [[ "$BACKEND" == "nvflare" ]]; then
    # Round boundaries.
    ROUND_STARTS="${WORK_DIR}/round_starts.tsv"
    ROUND_ENDS="${WORK_DIR}/round_ends.tsv"
    grep -oP '^\d+ .*Round \d+ started\.' "$FL_SERVER_TEXT" \
        | sed -E 's/^([0-9]+) .*Round ([0-9]+) started\..*/\2\t\1/' | sort -n -k1,1 > "$ROUND_STARTS" || true
    grep -oP '^\d+ .*Round \d+ finished\.' "$FL_SERVER_TEXT" \
        | sed -E 's/^([0-9]+) .*Round ([0-9]+) finished\..*/\2\t\1/' | sort -n -k1,1 > "$ROUND_ENDS" || true

    # Aggregation start/end are not tagged with a round number in the log text itself, so we
    # pair them up in chronological order (one Start/End pair per round, in round order —
    # true because ScatterAndGather aggregates synchronously once per round; see
    # scatter_and_gather.py lines ~207-267).
    AGG_STARTS="${WORK_DIR}/agg_starts.tsv"
    AGG_ENDS="${WORK_DIR}/agg_ends.tsv"
    grep -oP '^\d+ .*Start aggregation\.' "$FL_SERVER_TEXT" | awk '{print $1}' > "$AGG_STARTS" || true
    grep -oP '^\d+ .*End aggregation\.' "$FL_SERVER_TEXT" | awk '{print $1}' > "$AGG_ENDS" || true

    # Per-contribution lines. NVFLARE's IntimeModelSelector logs
    #   "Contribution from <client> ACCEPTED|REJECTED by the aggregator at round <N>."
    # (older builds omit " at round <N>", hence the optional group). The round tag lets us
    # break contributions out per-round in the table below.
    # NB: the `|| true` on these is load-bearing — with `set -o pipefail`, a zero-match grep
    # fails the whole pipeline and set -e would abort the script (wc still prints 0 first).
    CONTRIB_FILE="${WORK_DIR}/contributions.tsv"   # verdict \t round ('-' if untagged)
    grep -oP 'Contribution from \S+ (ACCEPTED|REJECTED) by the aggregator( at round \d+)?\.' "$FL_SERVER_TEXT" \
        | sed -E 's/^Contribution from [^ ]+ ([A-Z]+) by the aggregator( at round ([0-9]+))?\./\1\t\3/' \
        | sed -E 's/\t$/\t-/' > "$CONTRIB_FILE" || true
    ACCEPTED_TOTAL="$(grep -c $'^ACCEPTED\t' "$CONTRIB_FILE" || true)"
    REJECTED_TOTAL="$(grep -c $'^REJECTED\t' "$CONTRIB_FILE" || true)"

    n_rounds="$(wc -l < "$ROUND_STARTS" | tr -d ' ')"
    for i in $(seq 1 "$n_rounds"); do
        round_num="$(awk -v i="$i" 'NR==i{print $1}' "$ROUND_STARTS")"
        start_ms="$(awk -v i="$i" 'NR==i{print $2}' "$ROUND_STARTS")"
        end_ms="$(awk -v r="$round_num" '$1==r{print $2; exit}' "$ROUND_ENDS")"
        agg_start="$(awk -v i="$i" 'NR==i{print}' "$AGG_STARTS")"
        agg_end="$(awk -v i="$i" 'NR==i{print}' "$AGG_ENDS")"
        acc_r="$(awk -F'\t' -v r="$round_num" '$1=="ACCEPTED" && $2==r{n++} END{print n+0}' "$CONTRIB_FILE")"
        rej_r="$(awk -F'\t' -v r="$round_num" '$1=="REJECTED" && $2==r{n++} END{print n+0}' "$CONTRIB_FILE")"
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$round_num" "$start_ms" "${end_ms:-}" "${agg_start:-}" "${agg_end:-}" "$acc_r" "$rej_r" >> "$ROUND_TABLE_FILE"
    done
elif [[ "$BACKEND" == "flower" ]]; then
    # Flower: no round-boundary or aggregation-timing log lines exist in the platform code
    # (see flip-utils/flip/flower/metrics.py) — approximate each round's window from the
    # spread of "Forwarded metric ... (round N)" timestamps for that round.
    grep -oP '^\d+ .*\(round \d+\)' "$FL_SERVER_TEXT" "$FL_API_TEXT" 2>/dev/null \
        | sed -E 's/^[^0-9]*([0-9]+) .*\(round ([0-9]+)\).*/\2\t\1/' \
        | sort -n -k1,1 -k2,2 > "${WORK_DIR}/flower_rounds.tsv" || true
    if [[ -s "${WORK_DIR}/flower_rounds.tsv" ]]; then
        cut -f1 "${WORK_DIR}/flower_rounds.tsv" | sort -un | while read -r r; do
            start_ms="$(awk -v r="$r" -F'\t' '$1==r && (m==""||$2+0<m){m=$2+0} END{print m}' "${WORK_DIR}/flower_rounds.tsv")"
            end_ms="$(awk -v r="$r" -F'\t' '$1==r && (m==""||$2+0>m){m=$2+0} END{print m}' "${WORK_DIR}/flower_rounds.tsv")"
            printf '%s\t%s\t%s\t-\t-\t-\t-\n' "$r" "$start_ms" "$end_ms" >> "$ROUND_TABLE_FILE"
        done
    fi
fi

TOTAL_ROUNDS="$(wc -l < "$ROUND_TABLE_FILE" | tr -d ' ')"

# --------------------------------------------------------------------------------------
# Step 7b: persist the per-round table as rounds.tsv (ms-precision, machine-readable —
# the summary.md table truncates to whole seconds) and render the timing boxplot.
#
# The boxplot is best-effort: it needs pandas+seaborn, which are NOT dependencies of any
# FLIP service. Preferred runner is `uv run --no-project --with ...` (ephemeral env, no
# repo lockfile touched); falls back to a system python3 that already has both; skipped
# with a warning otherwise. A plot failure never fails the extraction.
# --------------------------------------------------------------------------------------

ROUNDS_TSV="${MODEL_OUT_DIR}/rounds.tsv"

# maybe_iso/maybe_dur: ROUND_TABLE_FILE uses "" (nvflare path) or "-" (flower path) for
# missing values — normalise both to "-" in the TSV.
maybe_iso() { [[ -n "${1:-}" && "${1:-}" != "-" ]] && ms_to_iso "$1" || echo "-"; }
maybe_dur() {
    if [[ -n "${1:-}" && "${1:-}" != "-" && -n "${2:-}" && "${2:-}" != "-" ]]; then
        awk -v a="$1" -v b="$2" 'BEGIN{printf "%.3f", (b - a) / 1000}'
    else
        echo "-"
    fi
}

{
    printf 'round\tstarted_utc\tfinished_utc\tduration_s\tagg_started_utc\tagg_finished_utc\tagg_duration_s\taccepted\trejected\tstart_ms\tend_ms\tagg_start_ms\tagg_end_ms\n'
    while IFS=$'\t' read -r round start_ms end_ms agg_start agg_end acc rej; do
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$round" \
            "$(maybe_iso "$start_ms")" "$(maybe_iso "$end_ms")" "$(maybe_dur "$start_ms" "$end_ms")" \
            "$(maybe_iso "$agg_start")" "$(maybe_iso "$agg_end")" "$(maybe_dur "$agg_start" "$agg_end")" \
            "${acc:--}" "${rej:--}" \
            "${start_ms:--}" "${end_ms:--}" "${agg_start:--}" "${agg_end:--}"
    done < "$ROUND_TABLE_FILE"
} > "$ROUNDS_TSV"
log "Wrote per-round table: ${ROUNDS_TSV} (${TOTAL_ROUNDS} round(s))"

# Mean / sample-std / n over a numeric-or-"-" column of rounds.tsv (1-based index).
# Emits "mean\tstd\tn" ("-\t-\t0" when no values). Used by summary section 4.
col_stats() {
    awk -F'\t' -v c="$1" 'NR > 1 && $c != "-" {x = $c + 0; s += x; ss += x*x; n++}
        END {
            if (n == 0) {print "-\t-\t0"; exit}
            m = s / n
            sd = (n > 1) ? sqrt((ss - n*m*m) / (n - 1)) : 0
            printf "%.3f\t%.3f\t%d\n", m, sd, n
        }' "$ROUNDS_TSV"
}

DUR_STATS="$(col_stats 4)"    # duration_s
AGG_STATS="$(col_stats 7)"    # agg_duration_s
# Inter-round gap: this round's start_ms (col 10) minus the previous round's end_ms
# (col 11); rows are in round order, and the first round has no predecessor.
GAP_STATS="$(awk -F'\t' 'NR > 1 && $10 != "-" && $11 != "-" {
        if (prev_end != "") {g = ($10 - prev_end) / 1000; s += g; ss += g*g; n++}
        prev_end = $11
    }
    END {
        if (n == 0) {print "-\t-\t0"; exit}
        m = s / n
        sd = (n > 1) ? sqrt((ss - n*m*m) / (n - 1)) : 0
        printf "%.3f\t%.3f\t%d\n", m, sd, n
    }' "$ROUNDS_TSV")"

BOXPLOT_FILE="${MODEL_OUT_DIR}/round_timings_boxplot.png"
PLOT_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/plot_round_timings.py"
BOXPLOT_GENERATED=false
if [[ "$TOTAL_ROUNDS" -gt 0 && -f "$PLOT_SCRIPT" ]]; then
    plot_cmd=()
    if command -v uv >/dev/null 2>&1; then
        plot_cmd=(uv run --no-project --with pandas --with seaborn python)
    elif command -v python3 >/dev/null 2>&1 && python3 -c 'import pandas, seaborn' >/dev/null 2>&1; then
        plot_cmd=(python3)
    fi
    if [[ ${#plot_cmd[@]} -gt 0 ]]; then
        if "${plot_cmd[@]}" "$PLOT_SCRIPT" "$ROUNDS_TSV" "$BOXPLOT_FILE" \
                --model-id "$MODEL_ID" --backend "$BACKEND" >&2; then
            BOXPLOT_GENERATED=true
            log "Wrote round-timing boxplot: ${BOXPLOT_FILE}"
        else
            warn "plot_round_timings.py failed — continuing without the boxplot (rounds.tsv is still written)."
        fi
    else
        warn "Neither uv nor a python3 with pandas+seaborn is available — skipping the boxplot (rounds.tsv is still written)."
    fi
elif [[ "$TOTAL_ROUNDS" -gt 0 ]]; then
    warn "Plot script not found at ${PLOT_SCRIPT} — skipping the boxplot."
fi

# --------------------------------------------------------------------------------------
# Step 8: metric #2 — update size (see header comment; per-round size is NOT available)
# --------------------------------------------------------------------------------------

UPLOAD_SIZE_LINES="${WORK_DIR}/upload_size_lines.txt"
grep -F "$MODEL_ID" "$FLIP_API_RECONSTRUCTED" \
    | grep -E "Size:|ContentLength|File has been added to the database" > "$UPLOAD_SIZE_LINES" || true

# --------------------------------------------------------------------------------------
# Step 9: metric #6 — deployment failures (errors, retries, dropouts, submission failures)
# --------------------------------------------------------------------------------------

FAILURES_FILE="${MODEL_OUT_DIR}/failures.tsv"
: > "$FAILURES_FILE"

{
    # flip-api: ERROR/WARNING lines mentioning this model, plus known scheduler failure
    # phrases within the model's attempt window(s) (these don't carry model_id themselves —
    # see fl_scheduler_service.py:503-509 — so they're only attributable by time overlap).
    grep -E '^\S+ \S+ *(ERROR|WARNING)' "$MODEL_LINES_FILE" | sed 's/^/flip-api\t/' || true

    while IFS=$'\t' read -r start_ms end_ms _status; do
        [[ -z "$start_ms" ]] && continue
        awk -v s="$start_ms" -v e="$end_ms" '($1+0)>=s && ($1+0)<=e' "$FLIP_API_RECONSTRUCTED" \
            | grep -E "Failed to start training:|Unsupported FL backend:|Database error|Failed to resolve|could not be resolved to a trust" \
            | sed 's/^/flip-api\t/' || true
    done < "$ATTEMPTS_FILE"

    # fl-api-net-*: known failure/retry phrases (fl-services/nvflare/fl-api-base/fl_api/
    # utils/flip_session.py and fl-services/flower/fl-api-flower/fl_api/app.py).
    grep -E "Session inactive, trying to reconnect|attempting to reconnect and retry|Retry after reconnect failed|Failed to run Flower|Flower .* failed|Failed to parse Flower|Flower run command failed" "$FL_API_TEXT" 2>/dev/null \
        | sed 's/^/fl-api\t/' || true

    # fl-server-net-*: client rejects / aborts (NVFLARE) — a REJECTED contribution or an
    # abort signal is evidence of a client-side dropout or protocol failure. A JobLogReceiver
    # "Saving live log 'error_log.txt' as 'ERRORLOG' from <client>" line means that client
    # produced an error log during the job — the error itself is only readable trust-side.
    grep -E "REJECTED by the aggregator|Abort signal received|as 'ERRORLOG' from|ERROR -|CRITICAL -" "$FL_SERVER_TEXT" 2>/dev/null \
        | sed 's/^/fl-server\t/' || true

    # flip-api: "no clients connected" / unresolvable client -> trust mapping issues.
    grep -E "No clients connected|not found in client statuses|Clients unavailable" "$FLIP_API_RECONSTRUCTED" 2>/dev/null \
        | sed 's/^/flip-api\t/' || true
} | sort -t$'\t' -k2,2 -n >> "$FAILURES_FILE" 2>/dev/null || true

N_FAILURES="$(wc -l < "$FAILURES_FILE" | tr -d ' ')"

# --------------------------------------------------------------------------------------
# Step 10: metric #7 — infrastructure constraints
# --------------------------------------------------------------------------------------

INFRA_FILE="${MODEL_OUT_DIR}/infra_constraints.tsv"
: > "$INFRA_FILE"

# NB: HTTP 5xx is matched only as an HTTP-access-log status (`HTTP/1.1" 5xx`), never as a
# bare number — the flattened lines are prefixed with an epoch-ms timestamp, and a bare
# `503` matches inside the timestamp digits of essentially every line (found the hard way).
INFRA_PATTERN='OutOfMemory|OOMKilled|MemoryError|No space left on device|disk quota|TimeoutError|timed? ?out|Connection refused|ConnectionError|ConnectionResetError|ECONNREFUSED|throttl|Rate exceeded|ResourceInitializationError|CannotPullContainerError|Service Unavailable|HTTP/1.1" 5[0-9][0-9]'

{
    grep -iE "$INFRA_PATTERN" "$FLIP_API_RECONSTRUCTED" 2>/dev/null | sed 's/^/flip-api\t/' || true
    grep -iE "$INFRA_PATTERN" "$FL_API_TEXT" 2>/dev/null | sed 's/^/fl-api\t/' || true
    grep -iE "$INFRA_PATTERN" "$FL_SERVER_TEXT" 2>/dev/null | sed 's/^/fl-server\t/' || true
} > "$INFRA_FILE" 2>/dev/null || true

N_INFRA="$(wc -l < "$INFRA_FILE" | tr -d ' ')"

# Best-effort ECS stopped-task check. ECS's own API only retains stopped-task metadata for
# roughly an hour, so this is only useful shortly after a run — it is NOT a log-based
# signal and is skipped gracefully (not a hard failure) if permissions are missing or the
# window is too old.
ECS_STOPPED_TASKS_FILE="${MODEL_OUT_DIR}/ecs_stopped_tasks.json"
ECS_CHECK_NOTE="Skipped (--skip-ecs-check)."
if [[ "$SKIP_ECS_CHECK" != "true" ]]; then
    ECS_CHECK_NOTE="No stopped tasks found (or none within the ECS API's ~1h retention window)."
    services=(flip-api)
    for grp in "${FL_API_LOG_GROUPS[@]:-}" "${FL_SERVER_LOG_GROUPS[@]:-}"; do
        [[ -z "$grp" ]] && continue
        services+=("$(basename "$grp")")
    done
    all_stopped="[]"
    for svc in "${services[@]}"; do
        arns="$(awscli ecs list-tasks --cluster "$ECS_CLUSTER" --service-name "$svc" --desired-status STOPPED --query 'taskArns' --output text 2>/dev/null || true)"
        [[ -z "$arns" || "$arns" == "None" ]] && continue
        # shellcheck disable=SC2086
        details="$(awscli ecs describe-tasks --cluster "$ECS_CLUSTER" --tasks $arns \
            --query 'tasks[].{service:`'"$svc"'`,stoppedAt:stoppedAt,stoppedReason:stoppedReason,containers:containers[].{name:name,reason:reason,exitCode:exitCode}}' 2>/dev/null || echo '[]')"
        all_stopped="$(jq -c -s 'add' <(echo "$all_stopped") <(echo "$details") 2>/dev/null || echo "$all_stopped")"
    done
    echo "$all_stopped" > "$ECS_STOPPED_TASKS_FILE"
    n_stopped="$(jq 'length' "$ECS_STOPPED_TASKS_FILE" 2>/dev/null || echo 0)"
    [[ "$n_stopped" -gt 0 ]] && ECS_CHECK_NOTE="Found ${n_stopped} stopped task record(s) — see ecs_stopped_tasks.json (not necessarily within this run's window; ECS retention is independent of the log window)."
else
    echo "[]" > "$ECS_STOPPED_TASKS_FILE"
fi

# --------------------------------------------------------------------------------------
# Step 11: write summary.md
# --------------------------------------------------------------------------------------

SUMMARY_FILE="${MODEL_OUT_DIR}/summary.md"

{
    echo "# FL Model Metrics — ${MODEL_ID}"
    echo
    echo "- **Source URL**: ${URL}"
    echo "- **Project ID**: ${PROJECT_ID}"
    echo "- **Model ID**: ${MODEL_ID}"
    echo "- **Generated**: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "- **AWS profile / region**: ${AWS_PROFILE_ARG} / ${AWS_REGION_ARG}"
    echo "- **Log groups queried**: ${FLIP_API_LOG_GROUP}, ${FL_API_LOG_GROUPS[*]:-<none found>}, ${FL_SERVER_LOG_GROUPS[*]:-<none found>}"
    echo "- **Detected FL backend** (from log content in-window): ${BACKEND}"
    echo "- **Model activity window (flip-api)**: $(ms_to_iso "$TRUE_START_MS") to $(ms_to_iso "$TRUE_END_MS")"
    echo "- **fl-api/fl-server query window (padded ±${PAD_MINUTES}m around training attempts)**: $(ms_to_iso "$FL_SPAN_START_MS") to $(ms_to_iso "$FL_SPAN_END_MS")"
    echo
    echo "> Correlation caveat: fl-api-net-*/fl-server-net-* log lines carry no model_id or"
    echo "> project_id (confirmed by source inspection — see the script header). Everything"
    echo "> below from those two log groups is attributed to this model purely by falling"
    echo "> inside its TRAINING_STARTED -> terminal-status window(s), padded by"
    echo "> ${PAD_MINUTES} minutes. If another job trained concurrently on the same net in that"
    echo "> window, its log lines would be indistinguishable from this model's."
    echo

    echo "## 1. Communication rounds"
    echo
    if [[ "$BACKEND" == "nvflare" && "$TOTAL_ROUNDS" -gt 0 ]]; then
        echo "**${TOTAL_ROUNDS} round(s) executed** (source: fl-server-net-* \`Round N started.\`/\`Round N finished.\` lines, NVFLARE ScatterAndGather controller)."
    elif [[ "$BACKEND" == "flower" && "$TOTAL_ROUNDS" -gt 0 ]]; then
        echo "**${TOTAL_ROUNDS} round(s) observed** (PARTIAL — source: fl-server/fl-api \`(round N)\` tags in forwarded-metric log lines; Flower has no explicit round-start/finish log line in the platform code, so this is the highest round number seen, not necessarily confirmed as complete)."
    else
        echo "**NOT AVAILABLE** — no round-tagged log lines were found for either backend in the queried window (fl-server-net-*/fl-api-net-* logs may be outside the padded window, expired past the 7-day retention, or the run never reached the FL server)."
    fi
    echo

    echo "## 2. Update size"
    echo
    echo "**NOT AVAILABLE for per-round model-weight updates.** No component on the hub side"
    echo "logs the byte size of an individual round's weight update — checked flip-api,"
    echo "fl-api (both backends), and flip-utils; only S3 \`ContentLength\` for the initial"
    echo "*model-file bundle* upload is logged (flip-api file_services/uploaded_file.py)."
    echo "This is a different artifact from a round's weight delta. Measuring per-round"
    echo "update size would need new instrumentation in fl-api's upload/aggregation path or"
    echo "on the trust side."
    if [[ -s "$UPLOAD_SIZE_LINES" ]]; then
        echo
        echo "Initial model-file upload size log line(s) found for this model (auxiliary, NOT per-round):"
        echo
        echo '```'
        cat "$UPLOAD_SIZE_LINES"
        echo '```'
    fi
    echo

    echo "## 3. Aggregation time"
    echo
    if [[ "$BACKEND" == "nvflare" && "$TOTAL_ROUNDS" -gt 0 ]]; then
        echo "See per-round table below (\`agg_start\`/\`agg_end\` columns; source: fl-server-net-* \`Start aggregation.\`/\`End aggregation.\` lines). Aggregation-start/end pairs are matched to rounds in chronological order (NVFLARE's ScatterAndGather aggregates synchronously once per round)."
    elif [[ "$BACKEND" == "flower" ]]; then
        echo "**NOT AVAILABLE for Flower** — no aggregation start/end log line exists in the platform code for this backend (stock \`flwr\` strategy internals aren't captured by FLIP's own logging)."
    else
        echo "**NOT AVAILABLE** — backend not identified from logs in-window."
    fi
    echo

    echo "## 4. Per-round communication time"
    echo
    if [[ "$TOTAL_ROUNDS" -gt 0 ]]; then
        echo "| Round | Started | Finished | Duration (s) | Agg. start | Agg. end | Agg. duration (s) | Accepted | Rejected |"
        echo "|---|---|---|---|---|---|---|---|---|"
        while IFS=$'\t' read -r round start_ms end_ms agg_start agg_end acc rej; do
            start_iso="-"; end_iso="-"; dur="-"; agg_s_iso="-"; agg_e_iso="-"; agg_dur="-"
            [[ -n "$start_ms" && "$start_ms" != "-" ]] && start_iso="$(ms_to_iso "$start_ms")"
            [[ -n "$end_ms" && "$end_ms" != "-" ]] && { end_iso="$(ms_to_iso "$end_ms")"; [[ -n "$start_ms" ]] && dur=$(( (end_ms - start_ms) / 1000 )); }
            if [[ -n "$agg_start" && "$agg_start" != "-" ]]; then
                agg_s_iso="$(ms_to_iso "$agg_start")"
                [[ -n "$agg_end" && "$agg_end" != "-" ]] && { agg_e_iso="$(ms_to_iso "$agg_end")"; agg_dur=$(( (agg_end - agg_start) / 1000 )); }
            fi
            echo "| ${round} | ${start_iso} | ${end_iso} | ${dur} | ${agg_s_iso} | ${agg_e_iso} | ${agg_dur} | ${acc:--} | ${rej:--} |"
        done < "$ROUND_TABLE_FILE"
        echo
        if [[ "$BACKEND" == "nvflare" ]]; then
            echo "Client contributions in-window: **${ACCEPTED_TOTAL:-0} accepted**, **${REJECTED_TOTAL:-0} rejected** (source: fl-server-net-* \`Contribution from <client> ACCEPTED|REJECTED by the aggregator at round <N>.\` lines; per-round counts in the table above — untagged lines from older NVFLARE builds only appear in the totals)."
        fi
        echo
        echo "Summary statistics (all rounds; std is the sample standard deviation):"
        echo
        echo "| Metric | Mean (s) | Std (s) | n |"
        echo "|---|---|---|---|"
        IFS=$'\t' read -r m sd n <<<"$DUR_STATS"
        echo "| Round duration | ${m} | ${sd} | ${n} |"
        IFS=$'\t' read -r m sd n <<<"$AGG_STATS"
        echo "| Aggregation | ${m} | ${sd} | ${n} |"
        IFS=$'\t' read -r m sd n <<<"$GAP_STATS"
        echo "| Inter-round gap | ${m} | ${sd} | ${n} |"
        echo
        echo "Machine-readable copy of this table: \`rounds.tsv\` (ms-precision timestamps and durations; the table above truncates to whole seconds)."
        if [[ "$BOXPLOT_GENERATED" == "true" ]]; then
            echo "Timing distributions: \`round_timings_boxplot.png\` (seaborn boxplots — one panel per metric: round duration, aggregation time, inter-round gap)."
        fi
    else
        echo "**NOT AVAILABLE** — no rounds detected (see section 1)."
    fi
    echo

    echo "## 5. Wall-clock time"
    echo
    echo "Full model-status timeline (source: flip-api \`Attempting to set the model's status... ID: <model_id>, Status: <status>\` lines, deduplicated on consecutive-identical status):"
    echo
    echo "| Timestamp (UTC) | Status |"
    echo "|---|---|"
    while IFS=$'\t' read -r ts status; do
        echo "| $(ms_to_iso "$ts") | ${status} |"
    done < "$STATUS_TIMELINE_DEDUP"
    echo
    total_s=$(( (TRUE_END_MS - TRUE_START_MS) / 1000 ))
    echo "Total observed span (first to last model-related flip-api log line): **${total_s}s** ($(ms_to_iso "$TRUE_START_MS") to $(ms_to_iso "$TRUE_END_MS"))."
    echo
    if [[ -s "$ATTEMPTS_FILE" ]]; then
        echo "Training attempt(s) (TRAINING_STARTED -> terminal status; more than one row means the model was retried):"
        echo
        echo "| Attempt | Training started | Ended | Terminal status | Duration (s) |"
        echo "|---|---|---|---|---|"
        i=0
        while IFS=$'\t' read -r s e status; do
            i=$((i+1))
            dur=$(( (e - s) / 1000 ))
            echo "| ${i} | $(ms_to_iso "$s") | $(ms_to_iso "$e") | ${status} | ${dur} |"
        done < "$ATTEMPTS_FILE"
    fi
    echo

    echo "## 6. Deployment failures"
    echo
    if [[ "$N_FAILURES" -gt 0 ]]; then
        echo "${N_FAILURES} failure/error/retry/dropout-related line(s) found (source \\t line):"
        echo
        echo '```'
        cat "$FAILURES_FILE"
        echo '```'
        echo
        echo "(Full detail: \`failures.tsv\` in this directory. fl-api/fl-server lines are time-correlated, not model_id-tagged — see the correlation caveat above.)"
    else
        echo "No failure/error/retry/dropout-related lines found in the queried window and log groups."
    fi
    echo

    echo "## 7. Practical infrastructure constraints"
    echo
    if [[ "$N_INFRA" -gt 0 ]]; then
        echo "${N_INFRA} line(s) matching resource-limit/timeout/throttling/connection-failure patterns (source \\t line):"
        echo
        echo '```'
        cat "$INFRA_FILE"
        echo '```'
    else
        echo "No log lines matched common infrastructure-constraint patterns (OOM, disk, timeout, throttling, connection-refused) in the queried window."
    fi
    echo
    echo "ECS stopped-task check (best-effort; ECS retains stopped-task metadata for roughly"
    echo "1 hour, independent of CloudWatch log retention — this is only informative for"
    echo "runs investigated shortly after they happened): ${ECS_CHECK_NOTE}"
    echo

    echo "## Data provenance"
    echo
    echo "| # | Metric | Availability | Source |"
    echo "|---|---|---|---|"
    echo "| 1 | Communication rounds | Full (NVFLARE) / Partial (Flower) / None | fl-server-net-* |"
    echo "| 2 | Update size (per round) | **Not available** (hub logs) | n/a — needs new instrumentation |"
    echo "| 3 | Aggregation time | Full (NVFLARE) / **Not available** (Flower) | fl-server-net-* |"
    echo "| 4 | Per-round communication time | Full (NVFLARE) / Partial (Flower) | fl-server-net-* |"
    echo "| 5 | Wall-clock time | Full | flip-api |"
    echo "| 6 | Deployment failures | Full, best-effort | flip-api + fl-api-net-* + fl-server-net-* |"
    echo "| 7 | Infrastructure constraints | Best-effort (log greps); ECS stopped-task check is time-limited | all three + ECS API |"
    echo
    echo "## Raw logs"
    echo
    echo "See \`logs/\` in this directory:"
    echo
    for f in "$FLIP_API_RAW" "$FLIP_API_RECONSTRUCTED" "$MODEL_LINES_FILE" "${FL_API_DUMPS[@]:-}" "${FL_SERVER_DUMPS[@]:-}"; do
        [[ -e "$f" ]] && echo "- \`logs/$(basename "$f")\`"
    done
} > "$SUMMARY_FILE"

log "Wrote summary: ${SUMMARY_FILE}"
log "Done. Output: ${MODEL_OUT_DIR}"
