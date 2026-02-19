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

# Check that DEV_IMAGES_DIR and DEV_DATAFRAME exist
# Usage: ./scripts/check-dev-paths.sh <base_dir> <images_dir> <dataframe_path>

set -euo pipefail

BASE_DIR="${1:?Error: BASE_DIR is required}"
IMAGES_DIR="${2:?Error: DEV_IMAGES_DIR is required}"
DATAFRAME="${3:?Error: DEV_DATAFRAME is required}"

# Resolve paths relative to base directory
cd "${BASE_DIR}"

if [[ ! -d "${IMAGES_DIR}" ]]; then
    echo "Error: DEV_IMAGES_DIR [${IMAGES_DIR}] does not exist."
    exit 1
fi

if [[ ! -f "${DATAFRAME}" ]]; then
    echo "Error: DEV_DATAFRAME [${DATAFRAME}] does not exist."
    exit 1
fi

echo "Dev paths validated successfully"
