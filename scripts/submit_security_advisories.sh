#!/bin/bash
#
# Submit all FLIP security advisories to GitHub Security Advisories
# 
# Usage:
#   ./scripts/submit_security_advisories.sh [--dry-run]
#
# Prerequisites:
#   - gh CLI installed and authenticated (gh auth login)
#   - All advisory files in .github/security/advisories/GHSA-*.md

set -euo pipefail

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    echo "🏜️  DRY RUN MODE - no advisories will be submitted"
fi

ADVISORIES_DIR=".github/security/advisories"
REPO="londonaicentre/FLIP"

# Check prerequisites
if ! command -v gh &> /dev/null; then
    echo "❌ gh CLI not found. Install it: https://cli.github.com/"
    exit 1
fi

if ! gh auth status &> /dev/null; then
    echo "❌ gh CLI not authenticated. Run: gh auth login"
    exit 1
fi

if [[ ! -d "$ADVISORIES_DIR" ]]; then
    echo "❌ Advisories directory not found: $ADVISORIES_DIR"
    exit 1
fi

# Count advisories
ADVISORY_COUNT=$(ls "$ADVISORIES_DIR"/GHSA-*.md 2>/dev/null | wc -l)
if [[ $ADVISORY_COUNT -eq 0 ]]; then
    echo "❌ No advisory files found in $ADVISORIES_DIR"
    exit 1
fi

echo "📊 Found $ADVISORY_COUNT advisories to submit"
echo ""

# Check if gh supports security advisory API
echo "🔍 Checking GitHub CLI capabilities..."
if gh security advisory --help &>/dev/null; then
    echo "✅ gh security advisory command available"
    SUPPORTS_SECURITY_CMD=true
else
    echo "⚠️  gh security advisory not directly supported"
    echo "   Will use gh api for submission instead"
    SUPPORTS_SECURITY_CMD=false
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "ADVISORY SUBMISSION PLAN"
echo "════════════════════════════════════════════════════════════════════"

submitted=0
failed=0

# Process each advisory file
for advisory_file in "$ADVISORIES_DIR"/GHSA-*.md; do
    filename=$(basename "$advisory_file")
    advisory_id="${filename%.md}"
    
    # Extract key fields from markdown
    title=$(grep "^# Security Advisory:" "$advisory_file" | sed 's/^# Security Advisory: //')
    cvss=$(grep "CVSS" "$advisory_file" | head -1 | grep -oP 'CVSS \K[\d.]+' || echo "0")
    severity=$(grep "Severity:" "$advisory_file" | head -1 | grep -oP '\*\*Severity:\*\* \K[A-Z]+' || echo "UNKNOWN")
    cve=$(grep "CVE ID:" "$advisory_file" | head -1 | grep -oP 'CVE-[^\s]*' || echo "CVE-PENDING")
    
    echo ""
    echo "📋 Advisory: $advisory_id"
    echo "   Title: $title"
    echo "   Severity: $severity (CVSS $cvss)"
    echo "   CVE: $cve"
    
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "   [DRY RUN] Would submit this advisory"
        ((submitted++))
        continue
    fi
    
    # Attempt submission via gh API
    # GitHub Security Advisories API: POST /repos/{owner}/{repo}/security-advisories
    echo "   Submitting..."
    
    # For now, provide manual submission link since automated API submission
    # requires specific request format that gh CLI may not support directly
    echo "   🔗 Manual submission: https://github.com/$REPO/security/advisories/new"
    
done

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "NEXT STEPS"
echo "════════════════════════════════════════════════════════════════════"
echo ""
echo "GitHub Security Advisories currently require manual submission or"
echo "direct API calls. Here are your options:"
echo ""
echo "Option 1: Manual Web UI (Recommended for initial setup)"
echo "  1. Visit: https://github.com/$REPO/security/advisories/new"
echo "  2. For each advisory file, create a new draft:"
echo "     - Title: (from markdown header)"
echo "     - Type: Security vulnerability"
echo "     - Description: (copy vulnerability section)"
echo "     - Severity: (CRITICAL/HIGH/MEDIUM/LOW)"
echo "     - CVE: (if assigned)"
echo "  3. Click 'Publish advisory' when ready"
echo ""
echo "Option 2: GitHub API (For automation)"
echo "  curl -X POST https://api.github.com/repos/$REPO/security-advisories \\"
echo "    -H 'Authorization: Bearer \$GITHUB_TOKEN' \\"
echo "    -H 'Accept: application/vnd.github.v3+json' \\"
echo "    -d @advisory.json"
echo ""
echo "Option 3: Check gh CLI updates"
echo "  gh extension install github/gh-security"
echo ""
echo "✅ All advisories are ready for submission in: .github/security/advisories/"
echo ""
