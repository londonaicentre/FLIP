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
# Run one Flower tutorial on the flwr SIMULATOR — no SuperLink container, no SuperNodes, no
# fl-api, no Docker; flwr starts a local SuperLink of its own on 127.0.0.1. The NVFLARE
# counterpart is `make sim` in each nvflare tutorial dir; this gives the Flower tutorials the
# same fast local path.
#
#   make -C fl-tutorials sim-tutorial TUTORIAL=3d_spleen_segmentation FL_BACKEND=flower
#
# The tutorial's app code is byte-identical to a platform run. Identity comes from
# context.node_config's `partition-id` (which the simulator populates and a deployed
# SuperNode accepts via --node-config), resolved by flip.flower.identity.client_identity,
# so nothing in app/ knows which runtime it is in.
#
# For the container path — the pre-merge check that exercises TLS, fl-api submit and
# SuperNode registration — use run-tutorial.sh instead.
#
# Exit status is the RUN's: 0 only when the SuperLink reports it finished:completed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
# Run in flip-utils' env so the app sees the same flip package a SuperNode image carries.
FLIP_UV=(uv run --project "$REPO_ROOT/flip-utils" --extra full)
# flwr resolves the `local` connection to a SuperLink on these two ports (flwr/cli/constant.py)
# and reuses whatever already answers on the control port — it does not check who started it.
CONTROL_PORT="${FLWR_LOCAL_CONTROL_API_PORT:-39093}"
RUNTIME_PORT="${FLWR_LOCAL_RUNTIME_API_PORT:-39091}"
CONTROL_ADDRESS="127.0.0.1:$CONTROL_PORT"

# flwr keeps a long-lived local SuperLink and Ray workers inherit ITS environment, not what we
# export — so a SuperLink left over from an earlier run silently ignores the DEV_* and
# WORKING_DIR values this script sets and the run dies with PermissionError: '/app'. Clear our
# own leftovers.
#
# Deliberately NOT `pkill -f flower-superlink`: on a host running the FLIP dev stack that also
# matches deploy-fl-server-net-*'s superlink, because container processes are visible in the host
# PID namespace. Kill only processes that are (a) not in a container and (b) from this checkout.
# (a) compares PID namespaces rather than grepping /proc/<pid>/cgroup for a runtime-specific
# string: every container runtime gives its processes their own PID namespace, whereas the cgroup
# path spells "docker-<id>.scope" only under docker's systemd driver (the cgroupfs driver writes
# /docker/<id>, kubelet /kubepods/...). An unreadable namespace link (a root-owned container
# process) skips the pid, so the check fails closed. (b) matches this checkout's flip-utils venv
# path, not the bare checkout path — a worktree under .claude/worktrees/ starts with it too.
STOPPED_ANY=""
stop_stale_superlinks() {
  local pid own_ns pid_ns command_line platform
  platform="$(uname -s)"
  case "$platform" in
    Linux) own_ns="$(readlink /proc/$$/ns/pid)" || return 1 ;;
    Darwin) ;; # Docker processes live in a VM, outside the macOS process table.
    *) return 0 ;; # Unknown process-isolation semantics: leave processes alone.
  esac
  for pid in $(pgrep -f "flwr-simulation|flwr-serverapp|flower-superlink" 2>/dev/null || true); do
    if [ "$platform" = Linux ]; then
      pid_ns="$(readlink "/proc/$pid/ns/pid" 2>/dev/null)" || continue
      [ "$pid_ns" = "$own_ns" ] || continue
      command_line="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" || continue
    else
      command_line="$(ps -ww -p "$pid" -o command= 2>/dev/null)" || continue
    fi
    printf '%s\n' "$command_line" | grep -Fq "$REPO_ROOT/flip-utils/" || continue
    kill "$pid" 2>/dev/null && { echo "   stopped stale simulator process $pid"; STOPPED_ANY=1; }
  done
}

# Is anything listening on <port>? `ss` when present — and an `ss` that fails counts as taken,
# because "cannot tell" must never read as "free"; a bare connect otherwise.
port_listening() {
  local out
  if command -v ss >/dev/null 2>&1; then
    out="$(ss -Hltn "sport = :$1" 2>&1)" || { echo "⚠️  ss could not probe :$1 (treating it as taken): $out" >&2; return 0; }
    [ -n "$out" ]
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
  fi
}

# Pid of the listener on <port> when ss can see it (own processes), else empty.
listener_pid() {
  command -v ss >/dev/null 2>&1 || return 0
  { ss -Hltnp "sport = :$1" 2>/dev/null || true; } | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2 || true
}

# A SuperLink we just signalled may hold its ports a moment longer. flwr probes the control
# port at once (a 0.4 s gRPC deadline) and adopts whatever answers, so the run could land on a
# process mid-shutdown ("Connection to the SuperLink is unavailable"); and it cannot start a
# replacement while the runtime port is still bound. Give it up to five seconds to let go of
# each, and refuse to go on if it has not.
wait_for_port_release() {
  local port pid _
  for port in "$@"; do
    for _ in $(seq 1 "${SIM_PORT_RELEASE_POLLS:-50}"); do port_listening "$port" || break; sleep 0.1; done
    if port_listening "$port"; then
      pid="$(listener_pid "$port")"
      echo "❌ the simulator process this script stopped still holds 127.0.0.1:$port (pid ${pid:-unknown})."
      echo "   Wait for it to exit (or kill -9 it) and re-run."
      return 1
    fi
  done
}

# Whatever still listens on the control port after our own cleanup was started by someone else
# — another checkout's sim, typically. `flwr run . local` would hand this run to it and the app
# would execute in THAT checkout's environment (its flip package, Python and numpy), with
# nothing in the output saying so but the venv paths in any traceback. Refuse instead.
refuse_foreign_superlink() {
  local port="$1" pid cmd
  port_listening "$port" || return 0
  pid="$(listener_pid "$port")"
  echo "❌ $CONTROL_ADDRESS is already served by a local SuperLink this checkout did not start."
  if [ -n "$pid" ]; then
    cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-200 || true)"
    echo "   pid $pid: ${cmd:-<cmdline unreadable>}"
    echo "   Stop it (kill $pid) or run the simulator from that checkout."
  else
    echo "   Its owner is not visible from here (another user, or \`ss\` could not tell) — find and stop it."
  fi
  echo "   The run would otherwise execute inside that SuperLink's environment, not this checkout's."
  return 1
}

# The run id `flwr run` prints ("Successfully started run <id>", flwr/cli/run/run.py).
run_id_from() {
  sed -n 's/.*Successfully started run \([0-9]\{1,\}\).*/\1/p' "$1" | head -1 | grep .
}

# One `flwr ls` query: prints the run's status (pending|starting|running|finished:<why>), or an
# empty line when the SuperLink knows no such run. flwr answers EVERY failure as
# {"success": false, "error-message": …} with exit 0, so the reply is parsed rather than
# scraped, and any other failure (SuperLink gone, version mismatch, a broken env) fails the
# query with that message. TTY_COMPATIBLE=0 stops rich colouring the JSON under FORCE_COLOR;
# whatever escapes remain are stripped before parsing.
run_status() {
  TTY_COMPATIBLE=0 NO_COLOR=1 "${FLIP_UV[@]}" flwr ls local --run-id "$1" --format json 2>&1 \
    | "${FLIP_UV[@]}" python -c '
import json, re, sys
text = re.sub(r"\x1b\[[0-9;]*m", "", sys.stdin.read())
start = text.find("{")
try:
    reply = json.loads(text[start:]) if start >= 0 else {}
except json.JSONDecodeError:
    sys.exit("flwr ls answered with something other than JSON:\n" + text.strip()[-600:])
if not reply.get("success"):
    error = reply.get("error-message") or text.strip()[-600:]
    if "Run ID not found" in error:
        print("")
    else:
        sys.exit("flwr ls failed: " + error.strip())
else:
    runs = reply.get("runs") or []
    print(runs[0].get("status", "") if runs else "")
'
}

# `flwr run --stream` returns 0 once the log stream closes, whatever became of the run — a
# simulation that dies mid-round still hands back success. The SuperLink's own record is the
# verdict, so poll it until the status is terminal and pass only finished:completed. Each poll
# first checks the SuperLink is still there: `flwr ls` would otherwise quietly start a fresh
# one and report the stale status the old one persisted.
assert_run_completed() {
  local run_id="$1" status="" _
  for _ in $(seq 1 "${SIM_STATUS_POLLS:-30}"); do
    if ! port_listening "$CONTROL_PORT"; then
      echo "❌ the SuperLink at $CONTROL_ADDRESS is gone — run $run_id's outcome is unknown (see the streamed log above)"
      return 1
    fi
    status="$(run_status "$run_id")" || { echo "❌ could not query run $run_id on the SuperLink at $CONTROL_ADDRESS"; return 1; }
    case "$status" in finished:*) break ;; esac
    sleep "${SIM_STATUS_POLL_SECS:-2}"
  done
  case "$status" in
    finished:completed) echo "✅ run $run_id finished:completed"; return 0 ;;
    "") echo "❌ the SuperLink at $CONTROL_ADDRESS knows no run $run_id"; return 1 ;;
    finished:*) echo "❌ run $run_id ended $status (see the streamed log above)"; return 1 ;;
    *) echo "❌ run $run_id is still '$status' after its log stream closed"; return 1 ;;
  esac
}

# Sourced for its functions by fl-tutorials/tests/test_sim_tutorial_exit_status.py
# (SIM_TUTORIAL_LIB=1). Executing with the flag set would otherwise be a silent, successful no-op.
if [ "${SIM_TUTORIAL_LIB:-}" = 1 ]; then
  [ "${BASH_SOURCE[0]}" != "$0" ] && return 0
  echo "❌ SIM_TUTORIAL_LIB=1 is for sourcing this script's functions, not for running it" >&2
  exit 2
fi

TUTORIAL="${1:-${TUTORIAL:-}}"
# Consume the tutorial name so any remaining args pass through to `flwr run` untouched
# (e.g. --run-config 'num-server-rounds=1').
[ $# -gt 0 ] && shift || true

list() { for d in "$HERE"/*/app; do basename "$(dirname "$d")"; done; }
if [ -z "$TUTORIAL" ]; then echo "Set TUTORIAL=<name>. Available:"; list | sed 's/^/  - /'; exit 1; fi
if [ ! -d "$HERE/$TUTORIAL/app" ]; then echo "❌ Unknown tutorial '$TUTORIAL'. Available:"; list | sed 's/^/  - /'; exit 1; fi

# Same per-tutorial dev data mapping as run-tutorial.sh — LOCAL_DEV reads these directly
# instead of the bind mounts the compose stack would provide.
DATA_ROOT="${SIM_DATA_ROOT:-$REPO_ROOT/fl-tutorials/data}"
# Run-config overrides the simulator has to supply in place of the platform's submit step.
RUN_CONFIG=""
case "$TUTORIAL" in
  3d_spleen_segmentation|3d_spleen_segmentation_evaluation)
    # The MSD build both backends read, honouring NUM_CASES.
    export DEV_IMAGES_DIR="$DATA_ROOT/spleen/images"
    export DEV_DATAFRAME="$DATA_ROOT/spleen/dataframe.csv"
    DATASET_TARGET=spleen ;;
  xray_classification)
    export DEV_IMAGES_DIR="$DATA_ROOT/xrays_mini_300/accession-resources"
    export DEV_DATAFRAME="$DATA_ROOT/xrays_mini_300/dataframe.csv"
    DATASET_TARGET=xray ;;
  ehr_risk_prediction)
    # Tabular-only tutorial: no images, so DEV_IMAGES_DIR stays unset (the app never reads it).
    # Every simulated site gets the same CSV; each ClientApp slices out its own person_id-modulo
    # partition, exactly as under the compose stack.
    export DEV_DATAFRAME="$DATA_ROOT/synthea/dataframe.csv"
    DATASET_TARGET=synthea ;;
  *) echo "❌ No data mapping for '$TUTORIAL'"; exit 1 ;;
esac
REQUIRED=("$DEV_DATAFRAME")
if [ -n "${DEV_IMAGES_DIR:-}" ]; then REQUIRED+=("$DEV_IMAGES_DIR"); fi
if [ "$TUTORIAL" = 3d_spleen_segmentation_evaluation ]; then
  # The evaluation ServerApp opens `<flip-job-dir>/<checkpoint>`. On the platform fl-api sets
  # flip-job-dir to the uploaded bundle's directory at submit time and config.toml names the
  # checkpoint; the simulator has neither, so point both at the checkpoint the spleen download
  # fetches (download-spleen-checkpoint) — the app code stays identical.
  CHECKPOINT_DIR="$DATA_ROOT/model_checkpoints"
  RUN_CONFIG="flip-job-dir=\"$CHECKPOINT_DIR\" checkpoint=\"model.pt\""
  REQUIRED+=("$CHECKPOINT_DIR/model.pt")
fi
for p in "${REQUIRED[@]}"; do
  if [ ! -e "$p" ]; then
    echo "❌ Dataset missing: $p"
    echo "   Run: make -C fl-tutorials download-${DATASET_TARGET}-data"
    exit 1
  fi
done

# The ServerApp writes results under $WORKING_DIR (default "/app/runs" — the path inside a
# SuperNode container, which does not exist and is not writable on the host). Point it at the
# same host directory run-tutorial.sh uses, or the run trains fine and then dies with
# PermissionError: '/app' at the results-writing step.
export WORKING_DIR="${WORKING_DIR:-$REPO_ROOT/fl-services/flower/runs}"
mkdir -p "$WORKING_DIR"

stop_stale_superlinks
[ -n "$STOPPED_ANY" ] && wait_for_port_release "$CONTROL_PORT" "$RUNTIME_PORT"
refuse_foreign_superlink "$CONTROL_PORT"

# How many simulated sites, from the tutorial's own flip-min-clients so the two cannot drift.
SITES="$(sed -n 's/^flip-min-clients[[:space:]]*=[[:space:]]*\([0-9]\{1,\}\).*/\1/p' \
  "$HERE/$TUTORIAL/pyproject.toml" | head -1)"
if [ -z "$SITES" ]; then echo "❌ No flip-min-clients in $TUTORIAL/pyproject.toml"; exit 1; fi

export LOCAL_DEV=true
echo "🧪 Simulating Flower tutorial '$TUTORIAL' (flwr simulator — no containers)"
echo "   sites=$SITES"
echo "   DEV_IMAGES_DIR=${DEV_IMAGES_DIR:-<unset: tabular-only tutorial>}"
echo "   DEV_DATAFRAME=$DEV_DATAFRAME"
echo "   WORKING_DIR=$WORKING_DIR"
[ -n "$RUN_CONFIG" ] && echo "   run-config: $RUN_CONFIG"

# `local` is the SuperLink connection flwr ships in its own default config (created on first
# use), so this works on a clean checkout with nothing to install or hand-add. The site count
# rides on --federation-config rather than a [tool.flwr.federations] block, because `flwr run`
# migrates such a block into the user's ~/.flwr/config.toml and REWRITES the pyproject.toml to
# comment it out (flwr/cli/config_migration.py) — which would dirty a tracked file every run.
cd "$HERE/$TUTORIAL"
# The streamed output is kept so the run id can be read back off it once the stream closes.
# Through the pipe stdout is no longer a terminal, so PYTHONUNBUFFERED keeps the streamed log
# lines arriving as they happen instead of block-buffered.
STREAM="$(mktemp)"
trap 'rm -f "$STREAM"' EXIT
# A later --run-config on the command line overrides the same keys, so "$@" comes last.
PYTHONUNBUFFERED=1 "${FLIP_UV[@]}" \
  flwr run . local --federation-config "num-supernodes=$SITES" --stream \
  ${RUN_CONFIG:+--run-config "$RUN_CONFIG"} "$@" 2>&1 | tee "$STREAM"
RUN_ID="$(run_id_from "$STREAM")" || { echo "❌ flwr run printed no run id — was the run submitted?"; exit 1; }
assert_run_completed "$RUN_ID"
