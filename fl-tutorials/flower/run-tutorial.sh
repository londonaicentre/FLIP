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
# Run one Flower tutorial on the standalone compose stack (fl-services/flower):
# bring up SuperLink + 2 SuperNodes + fl-api with the tutorial's dev data mounted,
# submit the job to the fl-api control plane, wait for it to finish (or fail), and
# tear down. Called by fl-tutorials/flower/Makefile's run-tutorial / run-all-tutorials.
set -euo pipefail

TUTORIAL="${1:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(git -C "$HERE" rev-parse --show-toplevel)"
COMPOSE="docker compose -f $REPO_ROOT/fl-services/flower/compose.dev.yml -p flwr"
WAIT_SECS="${WAIT_SECS:-900}"

list() { for d in "$HERE"/*/app; do basename "$(dirname "$d")"; done; }
if [ -z "$TUTORIAL" ]; then echo "Set TUTORIAL=<name>. Available:"; list | sed 's/^/  - /'; exit 1; fi
if [ ! -d "$HERE/$TUTORIAL/app" ]; then echo "❌ Unknown tutorial '$TUTORIAL'. Available:"; list | sed 's/^/  - /'; exit 1; fi

# Per-tutorial dev data (LOCAL_DEV reads DEV_IMAGES_DIR / DEV_DATAFRAME). numpy is
# self-contained; spleen + xray need their HF dataset downloaded first.
case "$TUTORIAL" in
  numpy) : ;;
  3d_spleen_segmentation|3d_spleen_segmentation_evaluation)
    export DEV_IMAGES_DIR="$HERE/data/spleen/accession-resources"
    export DEV_DATAFRAME="$HERE/data/spleen/sample_get_dataframe_response.csv" ;;
  xray_classification)
    export DEV_IMAGES_DIR="$HERE/data/xrays_mini_300/accession-resources"
    export DEV_DATAFRAME="$HERE/data/xrays_mini_300/sample_get_dataframe_response.csv" ;;
  *) echo "❌ No data mapping for '$TUTORIAL'"; exit 1 ;;
esac
if [ -n "${DEV_IMAGES_DIR:-}" ] && [ ! -d "$DEV_IMAGES_DIR" ]; then
  echo "❌ Dataset missing: $DEV_IMAGES_DIR"
  echo "   Run: make -C $REPO_ROOT/fl-tutorials download-$( [ "${TUTORIAL#xray}" != "$TUTORIAL" ] && echo xray || echo spleen )-data FL_BACKEND=flower"
  exit 1
fi

export DOCKER_GID="$(id -g)"
export WORKING_DIR="$REPO_ROOT/fl-services/flower/runs"
mkdir -p "$WORKING_DIR" && chmod 777 "$WORKING_DIR"

cleanup() { echo "🧹 Tearing down..."; $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "🚀 Flower tutorial '$TUTORIAL' — starting standalone stack..."
$COMPOSE up -d --remove-orphans

echo "⏳ Waiting for fl-api health..."
for _ in $(seq 1 30); do
  $COMPOSE exec -T fl-api python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=3)" >/dev/null 2>&1 && break
  sleep 2
done

echo "📤 Submitting '$TUTORIAL' to fl-api..."
RUN_ID="$($COMPOSE exec -T fl-api python -c "import urllib.request as u; print(u.urlopen(u.Request('http://localhost:8000/submit_run/$TUTORIAL', method='POST')).read().decode())")"
echo "   run id: $RUN_ID"

echo "⏳ Waiting up to ${WAIT_SECS}s for the run to reach a terminal status (polling fl-api /list_runs)..."
run_id_clean="$(printf '%s' "$RUN_ID" | tr -d '"[:space:]')"
deadline=$(( $(date +%s) + WAIT_SECS ))
last=""
while [ "$(date +%s)" -lt "$deadline" ]; do
  # /list_runs returns [{job_id, status}] with normalized JobStatus (PENDING/RUNNING/
  # FINISHED/FAILED/STOPPED). Match our run and act on terminal states.
  st="$($COMPOSE exec -T fl-api python -c "
import urllib.request as u, json
try:
    runs = json.loads(u.urlopen('http://localhost:8000/list_runs', timeout=5).read())
    print(next((r['status'] for r in runs if str(r['job_id']) == '$run_id_clean'), 'UNKNOWN'))
except Exception:
    print('POLLERR')" 2>/dev/null || echo POLLERR)"
  [ "$st" != "$last" ] && { echo "   status: $st"; last="$st"; }
  case "$st" in
    FINISHED) echo "✅ '$TUTORIAL' run FINISHED"; exit 0 ;;
    FAILED)   echo "❌ '$TUTORIAL' run FAILED"; $COMPOSE logs supernode-1 2>&1 | tail -15; exit 1 ;;
    STOPPED)  echo "⚠️  '$TUTORIAL' run STOPPED"; exit 0 ;;
  esac
  sleep 5
done
rounds="$($COMPOSE logs supernode-1 2>&1 | grep -c 'Push .AppOutputs.' || true)"
echo "⚠️  '$TUTORIAL' not terminal within ${WAIT_SECS}s (last status: ${last:-unknown}, ${rounds} ClientApp round(s) done). Increase WAIT_SECS for slow CPU jobs (e.g. spleen)."
exit 0
