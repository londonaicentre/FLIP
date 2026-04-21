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

# Get the repository root directory (go up one level from scripts/)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source "$(dirname "${BASH_SOURCE[0]}")/utils.sh"

log_info "========================================"
log_info "   FLIP Monorepo Secret Scanning"
log_info "========================================"
echo ""

# Check if truffleHog is installed
if ! command -v trufflehog &> /dev/null; then
    log_warn "TruffleHog is not installed."
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
        log_warn "Docker is available. Would you like to run the scan using Docker? (y/n)"
        read -r response
        if [[ "$response" =~ ^[Yy]$ ]]; then
            USE_DOCKER=true
        else
            exit 1
        fi
    else
        log_error "Please install TruffleHog to continue."
        exit 1
    fi
fi

cd "$REPO_ROOT"

log_info "Repository: $REPO_ROOT"
log_info "Scanning mode: Git history (entire monorepo)"
echo ""

# Create output directory for reports
REPORT_DIR="$REPO_ROOT/.security-reports"
mkdir -p "$REPORT_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
REPORT_FILE="$REPORT_DIR/trufflehog-scan-$TIMESTAMP.json"

log_warn "Starting TruffleHog scan..."
log_warn "This may take several minutes for a monorepo."
echo ""

# Run TruffleHog scan
if [ "$USE_DOCKER" = true ]; then
    # Run with Docker
    log_info "Running TruffleHog via Docker..."
    docker run --rm -v "$REPO_ROOT:/repo" \
        trufflesecurity/trufflehog:latest \
        git file:///repo \
        --json \
        --no-update \
        > "$REPORT_FILE" 2>&1 || true
else
    # Run with installed binary
    log_info "Running TruffleHog..."
    trufflehog git "file://$REPO_ROOT" \
        --json \
        --no-update \
        > "$REPORT_FILE" 2>&1 || true
fi

# Check results
if [ ! -s "$REPORT_FILE" ]; then
    log_success "Scan complete: No secrets detected!"
    echo ""
    rm -f "$REPORT_FILE"
    exit 0
else
    # Count findings
    FINDING_COUNT=$(grep -c "Raw" "$REPORT_FILE" 2>/dev/null || echo "0")

    if [ "$FINDING_COUNT" -eq 0 ]; then
        log_success "Scan complete: No secrets detected!"
        echo ""
        rm -f "$REPORT_FILE"
        exit 0
    else
        log_error "WARNING: $FINDING_COUNT potential secret(s) detected!"
        echo ""
        log_warn "Report saved to: $REPORT_FILE"
        echo ""
        log_warn "Review the findings:"
        echo "  cat $REPORT_FILE | jq '.'"
        echo ""
        log_warn "To see just the detected secrets:"
        echo "  cat $REPORT_FILE | jq -r '.Raw' | sort -u"
        echo ""

        # Show summary if jq is available
        if command -v jq &> /dev/null; then
            log_warn "Summary of findings:"
            cat "$REPORT_FILE" | jq -r 'select(.Raw != null) | "\(.DetectorName): \(.Raw[0:50])..."' | head -20
            echo ""
        fi

        log_error "Please review these findings before committing."
        exit 1
    fi
fi
