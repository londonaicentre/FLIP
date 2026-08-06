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
# Verify that the shared URL/path guards in the two fl-api services have not drifted.
#
# `safe_join` and `validate_bundle_url` are byte-identical copies in the NVFLARE and Flower
# fl-api services. They are the only SSRF and path-traversal guards in front of the server-side
# bundle fetch, so a fix applied to one copy and not the other silently leaves the second service
# vulnerable -- which is exactly what happened with the numeric-IP bypass in FLIP#893.
#
# They are copies rather than a shared module on purpose: the two services live in separate Docker
# build contexts, separate uv projects and separate published images, so extracting a shared
# distributable costs far more than it protects. This check is the cheaper guarantee.
#
# The whole files are NOT compared -- they legitimately differ (copyright header, and the Flower
# copy carries a flower-only validate_tutorial_folder_name plus its _SAFE_NAME constant). Only the
# shared function bodies are compared.
#
# Paths are relative to the repo root (this script lives in scripts/, so cd up one).
cd "$(dirname "$0")/.." || exit 1

NVFLARE_FILE="fl-services/nvflare/fl-api-base/fl_api/utils/validation.py"
FLOWER_FILE="fl-services/flower/fl-api-flower/fl_api/utils/validation.py"

# Functions that must stay identical between the two copies.
SHARED_FUNCTIONS=("safe_join" "validate_bundle_url")

status=0

for file in "$NVFLARE_FILE" "$FLOWER_FILE"; do
    if [ ! -f "$file" ]; then
        echo "ERROR: $file not found"
        exit 1
    fi
done

# Extract a single top-level function definition (def <name> ... up to the next top-level
# statement). Emitted to stdout so the caller can diff two extractions.
extract_function() {
    local file="$1" fname="$2"
    # `fname`, not `func`: gawk reserves `func` as a builtin and dies on -v func=...,
    # while mawk accepts it — so the obvious name passes locally and fails in CI.
    awk -v fname="$fname" '
        $0 ~ "^def " fname "\\(" { inside = 1 }
        inside && NR > 1 && /^[^ \t#)]/ && $0 !~ "^def " fname "\\(" && started { inside = 0 }
        inside { print; started = 1 }
    ' "$file"
}

for func in "${SHARED_FUNCTIONS[@]}"; do
    nvflare_body=$(extract_function "$NVFLARE_FILE" "$func")
    flower_body=$(extract_function "$FLOWER_FILE" "$func")

    if [ -z "$nvflare_body" ]; then
        echo "ERROR: could not find '$func' in $NVFLARE_FILE"
        status=1
        continue
    fi
    if [ -z "$flower_body" ]; then
        echo "ERROR: could not find '$func' in $FLOWER_FILE"
        status=1
        continue
    fi

    if [ "$nvflare_body" != "$flower_body" ]; then
        echo "ERROR: '$func' has drifted between the two fl-api copies."
        echo "  $NVFLARE_FILE"
        echo "  $FLOWER_FILE"
        echo "Diff (nvflare vs flower):"
        diff <(printf '%s\n' "$nvflare_body") <(printf '%s\n' "$flower_body") | sed 's/^/    /'
        echo "Resync by copying the corrected function into the other file."
        status=1
    fi
done

if [ "$status" -eq 0 ]; then
    echo "fl-api validation guards are in sync (${SHARED_FUNCTIONS[*]})."
fi

exit "$status"
