#!/usr/bin/env python3
#
# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pre-deployment verification script for AWS infrastructure code.

Validates that all critical files exist, have correct syntax, and that
Make targets are properly configured before running `make full-deploy`.

Exit Codes:
  0 - All checks passed
  1 - One or more checks failed
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def run_command(cmd: list[str]) -> tuple[bool, str]:
    """Run a shell command and return success status and output."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except FileNotFoundError:
        return False, f"Command not found: {cmd[0]}"


def check_file_exists(filepath: str, description: str) -> bool:
    """Check if a file exists."""
    path = Path(filepath)
    if path.exists():
        print(f"   ✅ {description}: {filepath}")
        return True
    else:
        print(f"   ❌ {description} NOT FOUND: {filepath}")
        return False


def check_python_syntax(filepath: str, description: str) -> bool:
    """Check Python file syntax."""
    success, _ = run_command(["python3", "-m", "py_compile", filepath])
    if success:
        print(f"   ✅ Python syntax valid: {description}")
        return True
    else:
        print(f"   ❌ Python syntax error: {description}")
        return False


def check_bash_syntax(filepath: str, description: str) -> bool:
    """Check Bash script syntax."""
    success, _ = run_command(["bash", "-n", filepath])
    if success:
        print(f"   ✅ Bash syntax valid: {description}")
        return True
    else:
        print(f"   ❌ Bash syntax error: {description}")
        return False


def check_makefile_target(target: str, description: str) -> bool:
    """Check if Make target exists."""
    with open("Makefile", "r") as f:
        content = f.read()
        if re.search(rf"^{re.escape(target)}:", content, re.MULTILINE):
            print(f"   ✅ Make target exists: {description}")
            return True
        else:
            print(f"   ❌ Make target missing: {description}")
            return False


def check_makefile_dependency(target: str, dependency: str, description: str) -> bool:
    """Check if Make target has a specific dependency."""
    with open("Makefile", "r") as f:
        content = f.read()
        # Look for lines like: target: dependency
        pattern = rf"^{re.escape(target)}:\s+[^#]*\b{re.escape(dependency)}\b"
        if re.search(pattern, content, re.MULTILINE):
            print(f"   ✅ Make dependency correct: {description}")
            return True
        else:
            print(f"   ❌ Make dependency missing: {description}")
            return False


def check_command_available(command: str, min_version: str | None = None) -> bool:
    """Check if a command is available in PATH."""
    result = subprocess.run(["which", command], capture_output=True, text=True, timeout=5)
    if result.returncode != 0:
        return False

    if min_version:
        result = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return False
        version_output = result.stdout + result.stderr
        # Extract the first dotted numeric version, supporting X.Y, X.Y.Z, or X.Y.Z.W formats.
        version_match = re.search(r"\d+(?:\.\d+)+", version_output)
        if version_match:
            actual_parts = [int(part) for part in version_match.group(0).split(".")]
            min_parts = [int(part) for part in min_version.split(".")]
            # Pad shorter list with zeros for uniform comparison
            max_len = max(len(actual_parts), len(min_parts))
            actual_parts.extend([0] * (max_len - len(actual_parts)))
            min_parts.extend([0] * (max_len - len(min_parts)))
            return actual_parts >= min_parts
    return True


def main() -> int:
    """Run all pre-deployment verification checks."""
    print("=" * 80)
    print("PRE-DEPLOYMENT VERIFICATION")
    print("=" * 80)
    print()

    all_passed = True

    # Check SSM prerequisites
    print("🔐 SSM SESSION MANAGER PREREQUISITES")
    if check_command_available("aws"):
        print("   ✅ AWS CLI installed")
    else:
        print("   ❌ AWS CLI not found - install via: brew install awscli")
        all_passed = False

    if check_command_available("session-manager-plugin", "1.2.319.0"):
        print("   ✅ Session Manager plugin installed (version >= 1.2.319.0)")
    else:
        print("   ❌ Session Manager plugin not found or outdated")
        print("      Install: brew install session-manager-plugin (macOS)")
        print("      Or: see deploy/providers/AWS/README.md#prerequisites")
        all_passed = False
    print()

    # Check Python files
    print("🐍 PYTHON FILES")
    all_passed &= check_file_exists("update_ssm_ssh_config.py", "SSH config script")
    all_passed &= check_python_syntax("update_ssm_ssh_config.py", "SSH config script")

    all_passed &= check_file_exists("check_status.py", "Status checker")
    all_passed &= check_python_syntax("check_status.py", "Status checker")

    all_passed &= check_file_exists("test_update_ssm_ssh_config.py", "Unit tests")
    all_passed &= check_python_syntax("test_update_ssm_ssh_config.py", "Unit tests")
    print()

    # Check Terraform files
    print("🏗️  TERRAFORM FILES")
    all_passed &= check_file_exists("main.tf", "Central Hub Terraform")
    all_passed &= check_file_exists("modules/trust_ec2/main.tf", "Trust EC2 Terraform")
    print()

    # Check templates
    print("📝 USER DATA TEMPLATES")
    print("   ℹ️  user_data is inlined in Terraform resources — no standalone template files to validate")
    print()

    # Check Ansible files
    print("🤖 ANSIBLE FILES")
    all_passed &= check_file_exists("site.yml", "Ansible playbook")
    print()

    # Check Makefile targets
    print("🎯 MAKEFILE TARGETS")
    all_passed &= check_makefile_target("ssh-config", "SSH config generation")
    all_passed &= check_makefile_target("check-ssm-ready", "SSM prerequisites check")
    all_passed &= check_makefile_target("ansible-init", "Ansible provisioning")
    all_passed &= check_makefile_target("deploy-centralhub", "Central Hub deployment")
    all_passed &= check_makefile_target("deploy-trust", "Trust deployment")
    all_passed &= check_makefile_target("full-deploy", "Full deployment")
    print()

    # Check Makefile dependencies
    print("🔗 MAKEFILE DEPENDENCIES")
    all_passed &= check_makefile_dependency(
        "ssh-config",
        "check-ssm-ready",
        "ssh-config depends on check-ssm-ready",
    )
    all_passed &= check_makefile_dependency(
        "deploy-centralhub",
        "ssh-config",
        "deploy-centralhub depends on ssh-config",
    )
    all_passed &= check_makefile_dependency(
        "deploy-trust",
        "ssh-config",
        "deploy-trust depends on ssh-config",
    )
    all_passed &= check_makefile_dependency(
        "full-deploy",
        "deploy-centralhub",
        "full-deploy depends on deploy-centralhub",
    )
    all_passed &= check_makefile_dependency(
        "full-deploy",
        "deploy-trust",
        "full-deploy depends on deploy-trust",
    )
    print()

    # Check unit tests
    print("🧪 UNIT TESTS")
    uv_available, _ = run_command(["which", "uv"])
    if not uv_available:
        print("   ⚠️  Unit tests skipped (uv not installed)")
    else:
        success, output = run_command(["uv", "run", "pytest", "test_update_ssm_ssh_config.py", "-q"])
        if success:
            if "passed" in output:
                print(f"   ✅ Unit tests passing: {output.strip()}")
            else:
                print("   ✅ Unit tests passing")
        else:
            print(f"   ❌ Unit tests FAILED:\n{output.strip()}")
            all_passed = False
    print()

    # Check documentation
    print("📚 DOCUMENTATION")
    all_passed &= check_file_exists("../../README.md", "Root README")
    all_passed &= check_file_exists("README.md", "AWS Provider README")
    print()

    # Check CI pipeline
    print("🔄 CI PIPELINE")
    all_passed &= check_file_exists("../../../.github/workflows/test_aws_deploy_scripts.yml", "AWS Deploy Scripts CI")
    print()

    # Summary
    print("=" * 80)
    if all_passed:
        print("✅ ALL CHECKS PASSED - READY FOR DEPLOYMENT")
        print("=" * 80)
        print()
        print("You can now run: make full-deploy")
        print()
        return 0
    else:
        print("❌ SOME CHECKS FAILED - PLEASE FIX ISSUES BEFORE DEPLOYMENT")
        print("=" * 80)
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
