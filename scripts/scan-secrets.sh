#!/usr/bin/env bash
# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get the repository root directory (go up one level from scripts/)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   FLIP Monorepo Secret Scanning${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if truffleHog is installed
if ! command -v trufflehog &> /dev/null; then
    echo -e "${YELLOW}TruffleHog is not installed.${NC}"
    echo ""
    echo "Installation options:"
    echo ""
    echo "1. Using Homebrew (macOS/Linux):"
    echo "   brew install trufflesecurity/trufflehog/trufflehog"
    echo ""
    echo "2. Using Docker:"
    echo "   docker pull trufflesecurity/trufflehog:latest"
    echo ""
    echo "3. Download binary from:"
    echo "   https://github.com/trufflesecurity/trufflehog/releases"
    echo ""
    
    # Check if Docker is available
    if command -v docker &> /dev/null; then
        echo -e "${YELLOW}Docker is available. Would you like to run the scan using Docker? (y/n)${NC}"
        read -r response
        if [[ "$response" =~ ^[Yy]$ ]]; then
            USE_DOCKER=true
        else
            exit 1
        fi
    else
        echo -e "${RED}Please install TruffleHog to continue.${NC}"
        exit 1
    fi
fi

cd "$REPO_ROOT"

echo -e "${BLUE}Repository:${NC} $REPO_ROOT"
echo -e "${BLUE}Scanning mode:${NC} Git history (entire monorepo)"
echo ""

# Create output directory for reports
REPORT_DIR="$REPO_ROOT/.security-reports"
mkdir -p "$REPORT_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
REPORT_FILE="$REPORT_DIR/trufflehog-scan-$TIMESTAMP.json"

echo -e "${YELLOW}Starting TruffleHog scan...${NC}"
echo -e "${YELLOW}This may take several minutes for a monorepo.${NC}"
echo ""

# Run TruffleHog scan
if [ "$USE_DOCKER" = true ]; then
    # Run with Docker
    echo -e "${BLUE}Running TruffleHog via Docker...${NC}"
    docker run --rm -v "$REPO_ROOT:/repo" \
        trufflesecurity/trufflehog:latest \
        git file:///repo \
        --json \
        --no-update \
        > "$REPORT_FILE" 2>&1 || true
else
    # Run with installed binary
    echo -e "${BLUE}Running TruffleHog...${NC}"
    trufflehog git "file://$REPO_ROOT" \
        --json \
        --no-update \
        > "$REPORT_FILE" 2>&1 || true
fi

# Check results
if [ ! -s "$REPORT_FILE" ]; then
    echo -e "${GREEN}✓ Scan complete: No secrets detected!${NC}"
    echo ""
    rm -f "$REPORT_FILE"
    exit 0
else
    # Count findings
    FINDING_COUNT=$(grep -c "Raw" "$REPORT_FILE" 2>/dev/null || echo "0")
    
    if [ "$FINDING_COUNT" -eq 0 ]; then
        echo -e "${GREEN}✓ Scan complete: No secrets detected!${NC}"
        echo ""
        rm -f "$REPORT_FILE"
        exit 0
    else
        echo -e "${RED}✗ WARNING: $FINDING_COUNT potential secret(s) detected!${NC}"
        echo ""
        echo -e "${YELLOW}Report saved to:${NC} $REPORT_FILE"
        echo ""
        echo -e "${YELLOW}Review the findings:${NC}"
        echo "  cat $REPORT_FILE | jq '.'"
        echo ""
        echo -e "${YELLOW}To see just the detected secrets:${NC}"
        echo "  cat $REPORT_FILE | jq -r '.Raw' | sort -u"
        echo ""
        
        # Show summary if jq is available
        if command -v jq &> /dev/null; then
            echo -e "${YELLOW}Summary of findings:${NC}"
            cat "$REPORT_FILE" | jq -r 'select(.Raw != null) | "\(.DetectorName): \(.Raw[0:50])..."' | head -20
            echo ""
        fi
        
        echo -e "${RED}Please review these findings before committing.${NC}"
        exit 1
    fi
fi
