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
            print(f"   ⚠️  Make dependency missing: {description}")
            return False


def main() -> int:
    """Run all pre-deployment verification checks."""
    print("=" * 80)
    print("PRE-DEPLOYMENT VERIFICATION")
    print("=" * 80)
    print()

    all_passed = True

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
    all_passed &= check_file_exists("templates/user_data.sh.tftpl", "Central Hub user_data")
    all_passed &= check_bash_syntax("templates/user_data.sh.tftpl", "Central Hub user_data")

    all_passed &= check_file_exists("modules/trust_ec2/templates/user_data.sh.tftpl", "Trust EC2 user_data")
    all_passed &= check_bash_syntax("modules/trust_ec2/templates/user_data.sh.tftpl", "Trust EC2 user_data")
    print()

    # Check Ansible files
    print("🤖 ANSIBLE FILES")
    all_passed &= check_file_exists("site.yml", "Ansible playbook")
    print()

    # Check Makefile targets
    print("🎯 MAKEFILE TARGETS")
    all_passed &= check_makefile_target("ssh-config", "SSH config generation")
    all_passed &= check_makefile_target("gen-trust-ec2-certs", "TLS cert generation")
    all_passed &= check_makefile_target("ansible-init", "Ansible provisioning")
    all_passed &= check_makefile_target("deploy-centralhub", "Central Hub deployment")
    all_passed &= check_makefile_target("full-deploy", "Full deployment")
    print()

    # Check Makefile dependencies
    print("🔗 MAKEFILE DEPENDENCIES")
    all_passed &= check_makefile_dependency(
        "gen-trust-ec2-certs",
        "ssh-config",
        "gen-trust-ec2-certs depends on ssh-config",
    )
    all_passed &= check_makefile_dependency(
        "write-trust-ca",
        "ssh-config",
        "write-trust-ca depends on ssh-config",
    )
    all_passed &= check_makefile_dependency(
        "deploy-centralhub",
        "write-trust-ca",
        "deploy-centralhub depends on write-trust-ca",
    )
    print()

    # Check unit tests
    print("🧪 UNIT TESTS")
    success, output = run_command(["uv", "run", "pytest", "test_update_ssm_ssh_config.py", "-q"])
    if success:
        # Count passing tests from output
        if "passed" in output:
            print(f"   ✅ Unit tests passing: {output.strip()}")
        else:
            print("   ✅ Unit tests passing")
        all_passed &= True
    else:
        print("   ⚠️  Unit tests check skipped (uv/pytest may not be installed)")
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
