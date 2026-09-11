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
# Run one Flower tutorial on the flwr SIMULATOR — no SuperLink, no SuperNodes, no fl-api,
# no Docker. The NVFLARE counterpart is `make sim` in each nvflare tutorial dir; this gives
# the Flower tutorials the same fast local path.
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
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
TUTORIAL="${1:-${TUTORIAL:-}}"
# Consume the tutorial name so any remaining args pass through to `flwr run` untouched
# (e.g. --run-config 'num-server-rounds=1').
[ $# -gt 0 ] && shift || true

list() { for d in "$HERE"/*/app; do basename "$(dirname "$d")"; done; }
if [ -z "$TUTORIAL" ]; then echo "Set TUTORIAL=<name>. Available:"; list | sed 's/^/  - /'; exit 1; fi
if [ ! -d "$HERE/$TUTORIAL/app" ]; then echo "❌ Unknown tutorial '$TUTORIAL'. Available:"; list | sed 's/^/  - /'; exit 1; fi

# Same per-tutorial dev data mapping as run-tutorial.sh — LOCAL_DEV reads these directly
# instead of the bind mounts the compose stack would provide.
DATA_ROOT="$REPO_ROOT/fl-tutorials/data"
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
  *) echo "❌ No data mapping for '$TUTORIAL'"; exit 1 ;;
esac
REQUIRED=("$DEV_IMAGES_DIR" "$DEV_DATAFRAME")
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

# flwr keeps a long-lived local SuperLink and Ray workers inherit ITS environment, not what we
# export here — so a SuperLink left over from an earlier run silently ignores the DEV_* and
# WORKING_DIR values above and the run dies with PermissionError: '/app'. Clear our own leftovers.
#
# Deliberately NOT `pkill -f flower-superlink`: on a host running the FLIP dev stack that also
# matches deploy-fl-server-net-*'s superlink, because container processes are visible in the host
# PID namespace. Kill only processes that are (a) not in a container and (b) from this checkout.
# (a) compares PID namespaces rather than grepping /proc/<pid>/cgroup for a runtime-specific
# string: every container runtime gives its processes their own PID namespace, whereas the cgroup
# path spells "docker-<id>.scope" only under docker's systemd driver (the cgroupfs driver writes
# /docker/<id>, kubelet /kubepods/...). An unreadable namespace link (a root-owned container
# process) skips the pid, so the check fails closed.
stop_stale_superlinks() {
  local pid own_ns pid_ns
  own_ns="$(readlink /proc/$$/ns/pid)"
  for pid in $(pgrep -f "flwr-simulation|flwr-serverapp|flower-superlink" 2>/dev/null || true); do
    pid_ns="$(readlink "/proc/$pid/ns/pid" 2>/dev/null)" || continue       # unreadable: not ours
    [ "$pid_ns" = "$own_ns" ] || continue                                  # containerised, not ours
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "$REPO_ROOT" || continue
    kill "$pid" 2>/dev/null && echo "   stopped stale simulator process $pid"
  done
}
stop_stale_superlinks

# How many simulated sites, from the tutorial's own flip-min-clients so the two cannot drift.
SITES="$(sed -n 's/^flip-min-clients[[:space:]]*=[[:space:]]*\([0-9]\{1,\}\).*/\1/p' \
  "$HERE/$TUTORIAL/pyproject.toml" | head -1)"
if [ -z "$SITES" ]; then echo "❌ No flip-min-clients in $TUTORIAL/pyproject.toml"; exit 1; fi

export LOCAL_DEV=true
echo "🧪 Simulating Flower tutorial '$TUTORIAL' (flwr simulator — no containers)"
echo "   sites=$SITES"
echo "   DEV_IMAGES_DIR=$DEV_IMAGES_DIR"
echo "   DEV_DATAFRAME=$DEV_DATAFRAME"
echo "   WORKING_DIR=$WORKING_DIR"
[ -n "$RUN_CONFIG" ] && echo "   run-config: $RUN_CONFIG"

# Run in flip-utils' env so the app sees the same flip package a SuperNode image carries.
#
# `local` is the SuperLink connection flwr ships in its own default config (created on first
# use), so this works on a clean checkout with nothing to install or hand-add. The site count
# rides on --federation-config rather than a [tool.flwr.federations] block, because `flwr run`
# migrates such a block into the user's ~/.flwr/config.toml and REWRITES the pyproject.toml to
# comment it out (flwr/cli/config_migration.py) — which would dirty a tracked file every run.
cd "$HERE/$TUTORIAL"
# A later --run-config on the command line overrides the same keys, so "$@" comes last.
exec uv run --project "$REPO_ROOT/flip-utils" --extra full \
  flwr run . local --federation-config "num-supernodes=$SITES" --stream \
  ${RUN_CONFIG:+--run-config "$RUN_CONFIG"} "$@"
