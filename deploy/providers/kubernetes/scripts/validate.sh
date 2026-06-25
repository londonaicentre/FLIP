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

# Validation script for the FLIP Helm chart.
# This script can be used as a pre-commit hook or in CI pipelines.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HELM="${HELM:-helm}"
KUBECONFORM="${KUBECONFORM:-kubeconform}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

failures=0

echo "=================================================="
echo "  FLIP Helm Chart Validation"
echo "  Chart: ${CHART_DIR}"
echo "=================================================="
echo ""

# Step 1: Chart.yaml version check
echo -n "Checking Chart.yaml version format... "
if grep -qE '^version: [0-9]+\.[0-9]+\.[0-9]+' "$CHART_DIR/Chart.yaml"; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC}"
    echo "  ERROR: Chart.yaml version must follow semver (e.g., 0.1.0)"
    failures=$((failures + 1))
fi

# Step 2: helm lint
echo -n "Running helm lint... "
if $HELM lint "$CHART_DIR" > /dev/null 2>&1; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC}"
    $HELM lint "$CHART_DIR" 2>&1 | sed 's/^/  /'
    failures=$((failures + 1))
fi

# Step 3: helm template (default backend - nvflare)
echo -n "Rendering templates (default/nvflare)... "
if $HELM template trust-release "$CHART_DIR" > /dev/null 2>&1; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC}"
    $HELM template trust-release "$CHART_DIR" 2>&1 | sed 's/^/  /'
    failures=$((failures + 1))
fi

# Step 4: helm template (flower backend)
echo -n "Rendering templates (flower backend)... "
if $HELM template trust-release "$CHART_DIR" --set flBackend=flower > /dev/null 2>&1; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC}"
    failures=$((failures + 1))
fi

# Step 5: helm template (all services disabled)
echo -n "Rendering templates (all services disabled)... "
if $HELM template trust-release "$CHART_DIR" \
    --set trustApi.enabled=false \
    --set imagingApi.enabled=false \
    --set dataAccessApi.enabled=false \
    --set flClient.enabled=false \
    --set omopDb.enabled=false \
    --set orthanc.enabled=false \
    --set xnat.enabled=false \
    --set observability.loki.enabled=false \
    --set observability.alloy.enabled=false \
    --set observability.grafana.enabled=false \
    > /dev/null 2>&1; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC}"
    failures=$((failures + 1))
fi

# Step 6: helm template with external service override
echo -n "Rendering templates (external omop-db override)... "
if $HELM template trust-release "$CHART_DIR" \
    --set omopDb.enabled=false \
    --set omopDb.external.host=test.example.com \
    > /dev/null 2>&1; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC}"
    failures=$((failures + 1))
fi

# Step 7: kubeconform validation (if available)
echo -n "Running kubeconform schema validation... "
if command -v $KUBECONFORM > /dev/null 2>&1; then
    if $HELM template trust-release "$CHART_DIR" | $KUBECONFORM --kubernetes-version 1.28 --strict > /dev/null 2>&1; then
        echo -e "${GREEN}PASS${NC}"
    else
        echo -e "${RED}FAIL${NC}"
        $HELM template trust-release "$CHART_DIR" | $KUBECONFORM --kubernetes-version 1.28 --strict 2>&1 | sed 's/^/  /'
        failures=$((failures + 1))
    fi
else
    echo -e "${YELLOW}SKIP${NC} (kubeconform not installed)"
fi

# Summary
echo ""
echo "=================================================="
if [ "$failures" -eq 0 ]; then
    echo -e "${GREEN}All validations passed!${NC}"
    exit 0
else
    echo -e "${RED}${failures} validation(s) failed.${NC}"
    exit 1
fi
