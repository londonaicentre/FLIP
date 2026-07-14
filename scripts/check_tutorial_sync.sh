#!/bin/bash
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
# Verify that Flower tutorial files copied from the fl-apps/flower/ templates have
# not drifted.
#
# These files cannot be symlinks: `flwr build` excludes symlinks from the FAB,
# so each tutorial keeps a real copy of the shared ServerApp and strategy. This
# check fails if a copy drifts from its fl-apps/flower/ template -- resync by
# copying the template over the tutorial file.
#
# The pyproject.toml files are intentionally NOT paired: the template
# pyprojects carry platform-only [tool.uv] tables (flip-utils pinned to the
# /opt/flip-utils source baked into the FL images, torch pinned to the cu128
# index -- FLIP#767) that must not reach the tutorials, whose pyprojects drive
# workstation venvs where /opt/flip-utils does not exist. Accepted trade-off:
# dependency-list drift between a tutorial pyproject and its template is no
# longer CI-guarded -- keep their [project] dependencies aligned by hand.
#
# Paths are relative to the repo root (this script lives in scripts/, so cd up one).
cd "$(dirname "$0")/.." || exit 1

# "<tutorial file>:<fl-apps/flower template>"
PAIRS=(
  "fl-tutorials/flower/3d_spleen_segmentation_evaluation/app/server_app.py:fl-apps/flower/evaluation/app/server_app.py"
  "fl-tutorials/flower/3d_spleen_segmentation_evaluation/app/strategy.py:fl-apps/flower/evaluation/app/strategy.py"
  "fl-tutorials/flower/3d_spleen_segmentation/app/server_app.py:fl-apps/flower/standard/app/server_app.py"
  "fl-tutorials/flower/3d_spleen_segmentation/app/strategy.py:fl-apps/flower/standard/app/strategy.py"
  "fl-tutorials/flower/xray_classification/app/server_app.py:fl-apps/flower/standard/app/server_app.py"
  "fl-tutorials/flower/xray_classification/app/strategy.py:fl-apps/flower/standard/app/strategy.py"
)

FAIL=0
for pair in "${PAIRS[@]}"; do
  tutorial_file="${pair%%:*}"
  template_file="${pair##*:}"
  if diff -q "$template_file" "$tutorial_file" >/dev/null 2>&1; then
    echo "ok:    $tutorial_file"
  else
    echo "DRIFT: $tutorial_file differs from $template_file" >&2
    diff "$template_file" "$tutorial_file" || true
    FAIL=1
  fi
done

if [ "$FAIL" -ne 0 ]; then
  echo "" >&2
  echo "Tutorial files have drifted from their fl-apps/flower/ templates." >&2
  echo "Resync by copying each fl-apps/flower/ template over the tutorial file." >&2
  exit 1
fi

echo ""
echo "All tutorial files are in sync with their fl-apps/flower/ templates."
