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
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   FLIP Monorepo Secret Scanning Setup${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Get the repository root directory (go up one level from scripts/)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "This script will set up secret scanning tools for the FLIP monorepo."
echo ""

# Check uv availability (project uses uv as package manager)
if ! command -v uv &> /dev/null; then
    echo -e "${YELLOW}Warning: uv is not installed. This project uses uv for package management.${NC}"
    echo "Install uv with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo ""
    exit 1
fi

# Install pre-commit and detect-secrets using uv
echo -e "${BLUE}Step 1: Installing pre-commit and detect-secrets...${NC}"
if ! uv tool install pre-commit 2>/dev/null; then
    echo -e "${YELLOW}pre-commit may already be installed, upgrading...${NC}"
    uv tool upgrade pre-commit || true
fi
if ! uv tool install detect-secrets 2>/dev/null; then
    echo -e "${YELLOW}detect-secrets may already be installed, upgrading...${NC}"
    uv tool upgrade detect-secrets || true
fi
echo -e "${GREEN}✓ pre-commit and detect-secrets installed via uv${NC}"
echo -e "${YELLOW}Note: Tools are installed in uv's tool directory (usually ~/.local/bin/)${NC}"
echo -e "${YELLOW}      Ensure this is in your PATH.${NC}"
echo ""

# Install TruffleHog
echo -e "${BLUE}Step 2: Installing TruffleHog...${NC}"
if [[ "$OSTYPE" == "darwin"* ]] && command -v brew &> /dev/null; then
    brew install trufflesecurity/trufflehog/trufflehog
    echo -e "${GREEN}✓ TruffleHog installed via Homebrew${NC}"
else
    echo -e "${YELLOW}To install TruffleHog:${NC}"
    echo "  • macOS: brew install trufflesecurity/trufflehog/trufflehog"
    echo "  • Linux: Download from https://github.com/trufflesecurity/trufflehog/releases"
    echo "  • Or use Docker: docker pull trufflesecurity/trufflehog:latest"
fi
echo ""

# Initialize pre-commit
echo -e "${BLUE}Step 3: Setting up pre-commit hooks...${NC}"
if command -v pre-commit &> /dev/null; then
    pre-commit install
    echo -e "${GREEN}✓ Pre-commit hooks installed${NC}"
    
    # Update pre-commit hooks to latest versions
    echo -e "${BLUE}Updating pre-commit hooks to latest versions...${NC}"
    pre-commit autoupdate
    echo -e "${GREEN}✓ Pre-commit hooks updated${NC}"
    
    # Create baseline for detect-secrets
    if command -v detect-secrets &> /dev/null; then
        echo -e "${BLUE}Creating detect-secrets baseline...${NC}"
        # Create baseline from scratch
        detect-secrets scan > .secrets.baseline 2>/dev/null || true
        echo -e "${GREEN}✓ Baseline created: .secrets.baseline${NC}"
    fi
else
    echo -e "${YELLOW}⊘ pre-commit command not found. Install it first.${NC}"
fi
echo ""

# Update .gitignore
echo -e "${BLUE}Step 4: Updating .gitignore...${NC}"
if ! grep -q ".security-reports" .gitignore 2>/dev/null; then
    echo "" >> .gitignore
    echo "# Security scan reports" >> .gitignore
    echo ".security-reports/" >> .gitignore
    echo -e "${GREEN}✓ Added .security-reports/ to .gitignore${NC}"
else
    echo -e "${GREEN}✓ .gitignore already configured${NC}"
fi
echo ""

# Make scan script executable
chmod +x "$REPO_ROOT/scripts/scan-secrets.sh"
echo -e "${GREEN}✓ Made scan-secrets.sh executable${NC}"
echo ""

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   Setup Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
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
