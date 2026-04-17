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

source "$(dirname "${BASH_SOURCE[0]}")/utils.sh"

log_info "========================================"
log_info "   FLIP Monorepo Secret Scanning Setup"
log_info "========================================"
echo ""

# Get the repository root directory (go up one level from scripts/)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "This script will set up secret scanning tools for the FLIP monorepo."
echo ""

# Check uv availability (project uses uv as package manager)
if ! command -v uv &> /dev/null; then
    log_warn "Warning: uv is not installed. This project uses uv for package management."
    echo "Install uv with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo ""
    exit 1
fi

# Install pre-commit and detect-secrets using uv
log_info "Step 1: Installing pre-commit and detect-secrets..."
if ! uv tool install pre-commit 2>/dev/null; then
    log_warn "pre-commit may already be installed, upgrading..."
    uv tool upgrade pre-commit || true
fi
if ! uv tool install detect-secrets 2>/dev/null; then
    log_warn "detect-secrets may already be installed, upgrading..."
    uv tool upgrade detect-secrets || true
fi
log_success "pre-commit and detect-secrets installed via uv"
log_warn "Note: Tools are installed in uv's tool directory (usually ~/.local/bin/)"
log_warn "      Ensure this is in your PATH."
echo ""

# Install TruffleHog
log_info "Step 2: Installing TruffleHog..."
if [[ "$OSTYPE" == "darwin"* ]] && command -v brew &> /dev/null; then
    brew install trufflesecurity/trufflehog/trufflehog
    log_success "TruffleHog installed via Homebrew"
else
    log_warn "To install TruffleHog:"
    echo "  • macOS: brew install trufflesecurity/trufflehog/trufflehog"
    echo "  • Linux: Download from https://github.com/trufflesecurity/trufflehog/releases"
    echo "  • Or use Docker: docker pull trufflesecurity/trufflehog:latest"
fi
echo ""

# Initialize pre-commit
log_info "Step 3: Setting up pre-commit hooks..."
if command -v pre-commit &> /dev/null; then
    pre-commit install
    log_success "Pre-commit hooks installed"

    # Update pre-commit hooks to latest versions
    log_info "Updating pre-commit hooks to latest versions..."
    pre-commit autoupdate
    log_success "Pre-commit hooks updated"

    # Create baseline for detect-secrets
    if command -v detect-secrets &> /dev/null; then
        log_info "Creating detect-secrets baseline..."
        # Create baseline from scratch
        detect-secrets scan > .secrets.baseline 2>/dev/null || true
        log_success "Baseline created: .secrets.baseline"
    fi
else
    log_warn "pre-commit command not found. Install it first."
fi
echo ""

# Update .gitignore
log_info "Step 4: Updating .gitignore..."
if ! grep -q ".security-reports" .gitignore 2>/dev/null; then
    echo "" >> .gitignore
    echo "# Security scan reports" >> .gitignore
    echo ".security-reports/" >> .gitignore
    log_success "Added .security-reports/ to .gitignore"
else
    log_success ".gitignore already configured"
fi
echo ""

# Make scan script executable
chmod +x "$REPO_ROOT/scripts/scan-secrets.sh"
log_success "Made scan-secrets.sh executable"
echo ""

log_success "========================================"
log_success "   Setup Complete!"
log_success "========================================"
echo ""
echo "Available commands:"
echo ""
echo "  1. Run full monorepo scan:"
echo "     ./scripts/scan-secrets.sh"
echo ""
echo "  2. Run pre-commit checks on staged files:"
echo "     pre-commit run"
echo ""
echo "  3. Run pre-commit checks on all files:"
echo "     pre-commit run --all-files"
echo ""
echo "  4. Scan with TruffleHog directly:"
echo "     trufflehog git file://. --only-verified"
echo ""
echo "  5. Scan specific subdirectories (e.g., flip-api):"
echo "     cd flip-api && trufflehog git file://. --only-verified"
echo ""
echo "GitHub Actions workflow should be added to:"
echo "  .github/workflows/secret-scanning.yml"
echo ""
