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

# Merge base app config templates with test app custom files
# With the flip package migration, base files are now in the flip library,
# so we only copy config templates and user custom files.
# Usage: ./scripts/merge-job-dirs.sh <base_app_dir> <test_app_dir> <output_dir>

set -euo pipefail

BASE_APP_DIR="${1:?Error: BASE_APP_DIR is required}"
TEST_APP_DIR="${2:?Error: TEST_APP_DIR is required}"
OUTPUT_DIR="${3:-.test_runs/merged-job-dir}"

echo "Merging job directories..."
echo "  Base config: ${BASE_APP_DIR}/config"
echo "  Test files: ${TEST_APP_DIR}"
echo "  Output: ${OUTPUT_DIR}"

# Clean and create output directory
rm -rf "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}/custom"
mkdir -p "${OUTPUT_DIR}/config"

# Copy ONLY config templates from base app (not custom folder with base files)
# Base application files are now in the flip package
if [[ -d "${BASE_APP_DIR}/config" ]]; then
    cp -r "${BASE_APP_DIR}/config"/* "${OUTPUT_DIR}/config/" 2>/dev/null || true
fi

# Copy any domain-specific files from base app custom directory if they exist
if [[ -d "${BASE_APP_DIR}/custom" ]]; then
    cp -r "${BASE_APP_DIR}/custom"/* "${OUTPUT_DIR}/custom/" 2>/dev/null || true
fi

# Copy test app files into custom subdirectory
if [[ -d "${TEST_APP_DIR}" ]]; then
    cp -r "${TEST_APP_DIR}"/* "${OUTPUT_DIR}/custom/" 2>/dev/null || true
fi

echo "Done. Output: ${OUTPUT_DIR}"
echo "Note: Base application files (controllers, components, executors) come from the flip package"
