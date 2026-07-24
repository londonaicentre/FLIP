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
#
# FLIP SessionStart hook: make a fresh Claude Code session (especially on the web)
# ready to test/lint. Best-effort only — it must never fail the session, so every
# step is guarded and the script always exits 0. Output is surfaced to Claude as
# session context.

set +e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 0

echo "== FLIP session bootstrap =="

# 1. Install pre-commit hooks (TruffleHog, detect-secrets, uv-lock, header/env checks).
if command -v pre-commit >/dev/null 2>&1; then
  if [ ! -f .git/hooks/pre-commit ]; then
    pre-commit install >/dev/null 2>&1 && echo "pre-commit: hooks installed" || echo "pre-commit: install skipped"
  else
    echo "pre-commit: already installed"
  fi
else
  echo "pre-commit: not on PATH (run 'pip install pre-commit' to enable commit-time guards)"
fi

# 2. Sync uv environments for the Python services this branch touched, so tests/linters
#    (ruff + mypy + pytest) can run immediately. Bounded + quiet; never blocks on network.
BASE="$(git merge-base HEAD origin/develop 2>/dev/null || echo develop)"
CHANGED_DIRS="$(git diff --name-only "$BASE" 2>/dev/null | grep -E '\.(py|toml)$' \
  | sed -E 's#/[^/]+$##' | sort -u)"

declare -A SYNCED
if command -v uv >/dev/null 2>&1; then
  for d in $CHANGED_DIRS; do
    # walk up to the nearest dir containing a pyproject.toml
    p="$d"
    while [ -n "$p" ] && [ "$p" != "." ]; do
      if [ -f "$p/pyproject.toml" ]; then
        if [ -z "${SYNCED[$p]}" ]; then
          SYNCED[$p]=1
          ( cd "$p" && timeout 120 uv sync --quiet >/dev/null 2>&1 ) \
            && echo "uv sync: $p ✓" || echo "uv sync: $p (skipped/timeout)"
        fi
        break
      fi
      p="$(dirname "$p")"
    done
  done
  [ ${#SYNCED[@]} -eq 0 ] && echo "uv sync: no changed Python projects on this branch"
else
  echo "uv: not on PATH"
fi

# 3. Report uv.lock drift so a forgotten 'make lock' surfaces before CI blocks the PR.
DRIFT="$(git status --porcelain '*uv.lock' 2>/dev/null)"
if [ -n "$DRIFT" ]; then
  echo "uv.lock drift detected (run 'make lock' and stage the changes):"
  echo "$DRIFT" | sed 's/^/  /'
fi

echo "== bootstrap done =="
exit 0
