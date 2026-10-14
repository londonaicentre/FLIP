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
# CPU-only contract check for one tutorial, on one or both backends.
#   bash .claude/skills/new-tutorial/scripts/check_tutorial.sh <name> [nvflare|flower|both]
# Runs: harness discovery, ruff over the tutorial dirs, the cross-cutting guard tests in
# fl-tutorials/tests/, scripts/check_tutorial_sync.sh, and (NVFLARE) `make export`, which builds
# the full job config without a GPU or data. It does NOT run the simulator.
set -uo pipefail

NAME="${1:?usage: check_tutorial.sh <tutorial-name> [nvflare|flower|both]}"
BACKENDS="${2:-both}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
FLT="$REPO_ROOT/fl-tutorials"
RUFF="${RUFF:-uvx ruff@0.14.7}"
PYTEST="uv run --project $REPO_ROOT/flip-utils --extra full --group dev pytest -q"
FAILED=0
step() { printf '\n==> %s\n' "$*"; }
fail() { printf '   ❌ %s\n' "$*"; FAILED=1; }
ok()   { printf '   ✅ %s\n' "$*"; }

want() { [ "$BACKENDS" = both ] || [ "$BACKENDS" = "$1" ]; }

DIRS=()
if want nvflare; then
  step "nvflare: harness discovery"
  LISTING="$(make -s -C "$FLT" list-tutorials FL_BACKEND=nvflare 2>/dev/null)"
  if printf '%s\n' "$LISTING" | grep -q -- "- $NAME "; then
    d="$(find "$FLT/nvflare" -mindepth 2 -maxdepth 2 -type d -name "$NAME" | head -1)"
    ok "listed ($d)"; DIRS+=("$d")
    [ -f "$d/.env.app" ] || fail ".env.app missing (discovery marker)"
    [ -f "$d/job.py" ] || fail "job.py missing"
    [ -d "$d/app_files" ] || fail "app_files/ missing"
    [ -f "$d/query.sql" ] || [ "$(grep -s '^JOB_TYPE=' "$d/.env.app" | cut -d= -f2)" = evaluation ] || fail "query.sql missing"
    jt="$(grep -s '^JOB_TYPE=' "$d/.env.app" | cut -d= -f2)"
    req="$REPO_ROOT/fl-apps/nvflare/$jt/required_files.json"
    if [ -f "$req" ]; then
      for f in $(python3 -c "import json,sys;print(' '.join(json.load(open(sys.argv[1]))))" "$req"); do
        [ -f "$d/app_files/$f" ] && ok "required $f present" || fail "required $f missing for job type $jt"
      done
    else
      fail "JOB_TYPE '$jt' has no fl-apps/nvflare template"
    fi
    step "nvflare: GPU-free job export"
    LOG="${TMPDIR:-/tmp}/check_tutorial_export.log"
    if make -s -C "$d" export >"$LOG" 2>&1; then ok "make export"; else
      fail "make export — last lines of $LOG:"; grep -v '^\s*$' "$LOG" | tail -3 | sed 's/^/      /'
    fi
  else
    fail "not listed by 'make -C fl-tutorials list-tutorials' — needs fl-tutorials/nvflare/<category>/$NAME/.env.app"
  fi
fi

if want flower; then
  step "flower: harness discovery"
  d="$FLT/flower/$NAME"
  if [ -d "$d/app" ]; then
    ok "app/ present"; DIRS+=("$d")
    [ -f "$d/pyproject.toml" ] || fail "pyproject.toml missing"
    grep -q '^flip-min-clients[[:space:]]*=[[:space:]]*[0-9]' "$d/pyproject.toml" 2>/dev/null \
      && ok "flip-min-clients declared" || fail "flip-min-clients missing from [tool.flwr.app.config]"
    grep -Eq "^[[:space:]]*([A-Za-z0-9_]+\|)*$NAME(\|[A-Za-z0-9_]+)*\)" "$FLT/flower/sim-tutorial.sh" \
      && ok "sim-tutorial.sh data mapping" || fail "no case entry for '$NAME' in fl-tutorials/flower/sim-tutorial.sh"
    jt="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['job_type'])" "$d/app/config.json" 2>/dev/null)"
    if [ -n "$jt" ] && [ -d "$REPO_ROOT/fl-apps/flower/$jt/app" ]; then
      for t in "$REPO_ROOT/fl-apps/flower/$jt/app/"*.py; do
        b="$(basename "$t")"; [ "$b" = __init__.py ] && continue
        cmp -s "$t" "$d/app/$b" && ok "$b byte-identical to template" || fail "$b differs from fl-apps/flower/$jt/app/$b"
      done
    else
      fail "app/config.json job_type '$jt' has no fl-apps/flower template"
    fi
  else
    fail "fl-tutorials/flower/$NAME/app/ missing (discovery marker)"
  fi
fi

if [ ${#DIRS[@]} -gt 0 ]; then
  step "ruff over the tutorial dirs"
  (cd "$FLT" && $RUFF check "${DIRS[@]}") && ok ruff || fail ruff
fi

step "cross-cutting guard tests (fl-tutorials/tests)"
GUARDS=(test_offline_apps.py test_fl_tutorials_make_targets.py)
want flower && GUARDS+=(test_flower_platform_parity.py test_flower_min_clients_wiring.py test_flwr_import_paths.py test_sim_tutorial_exit_status.py)
(cd "$FLT" && $PYTEST "${GUARDS[@]/#/tests/}") && ok "guards" || fail "guard tests"

step "scripts/check_tutorial_sync.sh"
(cd "$REPO_ROOT" && bash scripts/check_tutorial_sync.sh) && ok "sync" || fail "byte-identical pairs drifted"

step "catalogue mentions (informational)"
for f in docs/source/components/component-fl-nets.rst fl-tutorials/AGENTS.md fl-tutorials/README.md AGENTS.md flip-api/tests/demo_video.py flip-api/Makefile; do
  grep -q "$NAME" "$REPO_ROOT/$f" && ok "$f" || printf '   ⚠️  %s does not mention %s\n' "$f" "$NAME"
done

step "README visual (informational)"
for d in "${DIRS[@]}"; do
  grep -Eq '!\[|<img|```mermaid' "$d/README.md" 2>/dev/null && ok "$(basename "$d") README has a figure/diagram" \
    || printf '   ⚠️  %s README has no figure or diagram (SKILL.md §5)\n' "$d"
  grep -Eqi '^## .*expected performance' "$d/README.md" 2>/dev/null && ok "$(basename "$d") README has Expected performance" \
    || printf '   ⚠️  %s README has no "Expected performance" section (SKILL.md §5)\n' "$d"
  grep -Eqi '^## .*references' "$d/README.md" 2>/dev/null && ok "$(basename "$d") README has References" \
    || printf '   ⚠️  %s README has no "References" section (SKILL.md §5)\n' "$d"
done

echo
if [ "$FAILED" = 0 ]; then echo "✅ $NAME passes the CPU-only contract on: $BACKENDS (simulator not run)"; else echo "❌ $NAME: fix the items above"; exit 1; fi
