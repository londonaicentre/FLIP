#!/usr/bin/env python3
"""
Submit all FLIP security advisories to GitHub Security Advisories via gh CLI & API.

This script:
1. Parses advisory markdown files
2. Converts them to GitHub Security Advisory JSON format
3. Submits via gh CLI's API interface

Usage:
    python scripts/submit_advisories.py [--dry-run] [--only GHSA-ID]

Prerequisites:
    - gh CLI installed and authenticated
    - Advisory files in .github/security/advisories/GHSA-*.md
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


class AdvisoryParser:
    """Parse advisory markdown files."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.content = file_path.read_text()

    def extract_field(self, pattern: str) -> str | None:
        """Extract field matching regex pattern."""
        match = re.search(pattern, self.content, re.MULTILINE | re.DOTALL)
        return match.group(1).strip() if match else None

    def get_title(self) -> str:
        """Extract title from header."""
        return self.extract_field(r"^# Security Advisory: (.+)$") or "Untitled"

    def get_cvss(self) -> float:
        """Extract CVSS score."""
        cvss_str = self.extract_field(r"CVSS ([\d.]+)")
        return float(cvss_str) if cvss_str else 0.0

    def get_severity(self) -> str:
        """Extract severity level."""
        severity = self.extract_field(r"\*\*Severity:\*\* ([A-Z]+)")
        return (severity or "MEDIUM").lower()

    def get_cve(self) -> str | None:
        """Extract CVE ID."""
        cve = self.extract_field(r"\*\*CVE ID:\*\* (CVE-[\d\-]+)")
        return cve if cve and cve != "CVE-PENDING" else None

    def get_summary(self) -> str:
        """Extract vulnerability summary."""
        # Take first paragraph from vulnerability section
        vuln = self.extract_field(r"## Vulnerability\s+(.+?)(?:###|##|$)")
        if vuln:
            # Clean markdown and take first 200 chars
            text = re.sub(r"[*_#]", "", vuln).strip()
            lines = text.split("\n")
            return " ".join(lines)[:500]
        return ""

    def get_description(self) -> str:
        """Extract full vulnerability description."""
        vuln = self.extract_field(r"## Vulnerability\s+(.+?)(?:\n## |\n###|$)")
        if vuln:
            return re.sub(r"^#+\s+", "", vuln, flags=re.MULTILINE).strip()
        return self.get_summary()

    def get_remediation(self) -> str:
        """Extract remediation guidance."""
        remediation = self.extract_field(r"### Immediate Actions.*?\n(.+?)(?:\n### |\n##|$)")
        if remediation:
            return re.sub(r"^#+\s+", "", remediation, flags=re.MULTILINE).strip()
        return ""

    def get_affected_versions(self) -> list:
        """Extract affected versions."""
        affected = self.extract_field(r"### Affected Versions\s+(.+?)(?:\n###|\n##|$)")
        if affected:
            # Extract version specifiers
            versions = re.findall(r"[\d\.]+|[<>=~]+\s*[\d\.]+", affected)
            return versions or ["all"]
        return ["all"]

    def get_ghsa_id(self) -> str:
        """Extract GHSA ID."""
        return self.extract_field(r"\*\*Advisory ID:\*\* (GHSA-[a-z0-9\-]+)") or ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to API-ready dictionary."""
        return {
            "ghsa_id": self.get_ghsa_id(),
            "title": self.get_title(),
            "summary": self.get_summary(),
            "description": self.get_description(),
            "severity": self.get_severity(),
            "cvss_score": self.get_cvss(),
            "cve_id": self.get_cve(),
            "affected_versions": self.get_affected_versions(),
            "remediation": self.get_remediation(),
            "file": self.file_path.name,
        }


def submit_via_gh_api(repo: str, advisory: dict[str, Any], dry_run: bool = False) -> bool:
    """Submit advisory using gh CLI API."""
    endpoint = f"repos/{repo}/security-advisories"

    # Build request payload
    payload = {
        "title": advisory["title"],
        "summary": advisory["summary"],
        "description": advisory["description"],
        "severity": advisory["severity"],
        "publication_date": None,  # Auto-publish when submitted
    }

    if advisory["cve_id"]:
        payload["cve_id"] = advisory["cve_id"]

    if dry_run:
        print("\n📋 DRY RUN - Advisory payload:")
        print(json.dumps(payload, indent=2))
        return True

    try:
        # Convert to JSON and pass to gh api
        result = subprocess.run(
            ["gh", "api", endpoint, "-X", "POST", "-f", json.dumps(payload)],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode == 0:
            print(f"✅ Successfully submitted: {advisory['title']}")
            response = json.loads(result.stdout)
            print(f"   Advisory URL: {response.get('html_url', 'N/A')}")
            return True
        else:
            print(f"❌ Failed to submit: {advisory['title']}")
            print(f"   Error: {result.stderr}")
            return False

    except Exception as e:
        print(f"❌ Error submitting advisory: {e}")
        return False


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Submit FLIP security advisories to GitHub")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be submitted without actually submitting",
    )
    parser.add_argument("--only", help="Submit only specific advisory (e.g., GHSA-xxxx-xxxx-0001)")
    args = parser.parse_args()

    # Config
    advisories_dir = Path(".github/security/advisories")
    repo = "londonaicentre/FLIP"

    # Validation
    if not advisories_dir.exists():
        print(f"❌ Directory not found: {advisories_dir}")
        return 1

    # Get advisory files
    advisory_files = sorted(advisories_dir.glob("GHSA-*.md"))
    if not advisory_files:
        print("❌ No advisory files found")
        return 1

    if args.only:
        advisory_files = [f for f in advisory_files if args.only in f.name]
        if not advisory_files:
            print(f"❌ No advisory found matching: {args.only}")
            return 1

    # Check gh CLI
    try:
        result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, check=True)
        print("✅ Authenticated to GitHub\n")
    except subprocess.CalledProcessError:
        print("❌ gh CLI not authenticated. Run: gh auth login")
        return 1

    # Parse advisories
    print(f"📊 Found {len(advisory_files)} advisories\n")

    advisories = []
    for file_path in advisory_files:
        try:
            parser = AdvisoryParser(file_path)
            advisory = parser.to_dict()
            advisories.append(advisory)
            print(f"✅ Parsed {file_path.name}")
        except Exception as e:
            print(f"⚠️  Failed to parse {file_path.name}: {e}")

    if not advisories:
        print("❌ No advisories parsed successfully")
        return 1

    # Submit
    print(f"\n{'=' * 70}")
    if args.dry_run:
        print("🏜️  DRY RUN MODE - Showing submission payloads")
    else:
        print("📤 SUBMITTING ADVISORIES")
    print("=" * 70)

    submitted = 0
    for advisory in advisories:
        print(f"\n📋 {advisory['title']}")
        print(f"   Severity: {advisory['severity'].upper()} (CVSS {advisory['cvss_score']})")
        print(f"   CVE: {advisory['cve_id'] or 'PENDING'}")

        if submit_via_gh_api(repo, advisory, dry_run=args.dry_run):
            submitted += 1

    # Summary
    print(f"\n{'=' * 70}")
    print(f"✅ Submitted: {submitted}/{len(advisories)} advisories")
    print("=" * 70)

    if args.dry_run:
        print("\n💡 Tip: Run without --dry-run to actually submit to GitHub\n")
        return 0
    else:
        print(f"\n🔗 View advisories: https://github.com/{repo}/security/advisories\n")
        return 0 if submitted == len(advisories) else 1


if __name__ == "__main__":
    sys.exit(main())
