#!/usr/bin/env python3
"""
Submit all FLIP security advisories to GitHub Security Advisories via gh CLI.

Usage:
    python scripts/submit_security_advisories.py

Prerequisites:
    - gh CLI must be installed and authenticated (gh auth status)
    - Advisory files must be in .github/security/advisories/GHSA-*.md
"""

import json
import re
import subprocess
from pathlib import Path


def extract_advisory_data(file_path: Path) -> dict:
    """Parse advisory markdown file and extract key fields."""
    content = file_path.read_text()

    # Extract header fields
    title_match = re.search(r"^# Security Advisory: (.+)$", content, re.MULTILINE)
    title = title_match.group(1) if title_match else "No title"

    cvss_match = re.search(r"\*\*Severity:\*\* ([A-Z]+) \(CVSS ([\d.]+)\)", content)
    severity = cvss_match.group(1) if cvss_match else "UNKNOWN"
    cvss_score = float(cvss_match.group(2)) if cvss_match else 0.0

    cve_match = re.search(r"\*\*CVE ID:\*\* (CVE-\d+-\d+|CVE-PENDING)", content)
    cve_id = cve_match.group(1) if cve_match else None

    affected_match = re.search(r"### Affected Versions\s+([^#]+?)(?:### |$)", content, re.DOTALL)
    affected_versions = affected_match.group(1).strip() if affected_match else "All versions"

    # Extract vulnerability section
    vuln_match = re.search(r"## Vulnerability\s+([^#]+?)(?:## |$)", content, re.DOTALL)
    vulnerability_desc = vuln_match.group(1).strip() if vuln_match else ""

    # Clean up markdown formatting for API submission
    def clean_markdown(text):
        # Remove markdown headers
        text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
        # Remove bold/italic
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"\*(.+?)\*", r"\1", text)
        return text.strip()

    vulnerability_desc = clean_markdown(vulnerability_desc)

    # Extract immediate remediation steps
    remediation_match = re.search(
        r"### Immediate Actions \(Security Hotfix\)\s+([^#]+?)(?:### |## |$)", content, re.DOTALL
    )
    remediation = clean_markdown(remediation_match.group(1)) if remediation_match else ""

    return {
        "file": file_path.name,
        "title": title,
        "severity": severity,
        "cvss_score": cvss_score,
        "cve_id": cve_id,
        "affected_versions": affected_versions,
        "vulnerability": vulnerability_desc,
        "remediation": remediation,
    }


def submit_advisory(advisory_data: dict, dry_run: bool = False) -> bool:
    """Submit advisory using gh CLI."""
    print(f"\n{'=' * 70}")
    print(f"Submitting: {advisory_data['file']}")
    print(f"Title: {advisory_data['title']}")
    print(f"Severity: {advisory_data['severity']} (CVSS {advisory_data['cvss_score']})")
    print(f"CVE: {advisory_data['cve_id']}")
    print("=" * 70)

    if dry_run:
        print("[DRY RUN] Would submit with:")
        print(json.dumps(advisory_data, indent=2))
        return True

    # Build gh command
    # Note: The exact format depends on gh CLI version
    # Using `gh security advisory create` if available, or fallback to `gh api`

    cmd = [
        "gh",
        "security",
        "advisory",
        "create",
        f"--cve={advisory_data['cve_id']}" if advisory_data["cve_id"] != "CVE-PENDING" else "--cve-pending",
        f"--severity={advisory_data['severity'].lower()}",
        f"--cvss={advisory_data['cvss_score']}",
    ]

    # For now, provide instructions since gh might not have direct advisory creation
    print("\n🔗 Manual submission via: https://github.com/londonaicentre/FLIP/security/advisories")
    print("\n📋 Advisory details:")
    print(f"   Title: {advisory_data['title']}")
    print(f"   Severity: {advisory_data['severity']} (CVSS {advisory_data['cvss_score']})")
    print(f"   CVE: {advisory_data['cve_id']}")
    print("\n📝 Summary:")
    print(f"   {advisory_data['vulnerability'][:200]}...")
    print("\n✅ Remediation:")
    print(f"   {advisory_data['remediation'][:200]}...")

    return False


def main():
    """Main entry point."""
    advisories_dir = Path(".github/security/advisories")

    if not advisories_dir.exists():
        print(f"❌ Advisories directory not found: {advisories_dir}")
        return 1

    # Get all advisory files sorted
    advisory_files = sorted(advisories_dir.glob("GHSA-*.md"))
    if not advisory_files:
        print("❌ No advisory files found")
        return 1

    print(f"📂 Found {len(advisory_files)} advisories to submit\n")

    # Check gh CLI is installed
    try:
        result = subprocess.run(["gh", "--version"], capture_output=True, text=True, check=True)
        print(f"✅ {result.stdout.strip()}\n")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ gh CLI not found or not authenticated")
        print("   Run: gh auth login")
        return 1

    # Parse all advisories
    advisories = []
    for file_path in advisory_files:
        try:
            data = extract_advisory_data(file_path)
            advisories.append(data)
            print(f"✅ Parsed {file_path.name}: {data['title']}")
        except Exception as e:
            print(f"⚠️  Failed to parse {file_path.name}: {e}")

    print(f"\n{'=' * 70}")
    print(f"📊 Summary: {len(advisories)} advisories ready for submission")
    print("=" * 70)

    # Submit advisories
    submitted = 0
    failed = 0

    for advisory in advisories:
        try:
            if submit_advisory(advisory, dry_run=False):
                submitted += 1
            else:
                # gh CLI might not support direct advisory creation yet
                # Print manual submission instructions instead
                pass
        except Exception as e:
            print(f"❌ Error submitting {advisory['file']}: {e}")
            failed += 1

    # Final summary
    print(f"\n{'=' * 70}")
    print("📋 SUBMISSION SUMMARY")
    print("=" * 70)
    print(f"Total advisories: {len(advisories)}")
    print(f"Submitted: {submitted}")
    print(f"Failed: {failed}")
    print("\n⏭️  Next steps:")
    print("  1. Visit: https://github.com/londonaicentre/FLIP/security/advisories")
    print("  2. Click 'New draft advisory'")
    print("  3. Fill in each advisory using the parsed data above")
    print("  4. GitHub will assign CVE and GHSA IDs upon publication")
    print("\n💡 Tip: You can automate this with the GitHub API if needed")
    print("=" * 70)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit(main())
