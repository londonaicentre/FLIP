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

"""Unit tests for deploy readiness verification script."""

import tempfile
from pathlib import Path

from verify_deploy_readiness import (
    check_bash_syntax,
    check_file_exists,
    check_makefile_dependency,
    check_makefile_target,
    check_python_syntax,
    run_command,
)


class TestRunCommand:
    """Tests for the run_command helper function."""

    def test_run_command_success(self) -> None:
        """Test running a successful command."""
        success, output = run_command(["echo", "hello"])
        assert success is True
        assert "hello" in output

    def test_run_command_failure(self) -> None:
        """Test running a failing command."""
        success, output = run_command(["false"])
        assert success is False

    def test_run_command_not_found(self) -> None:
        """Test running a non-existent command."""
        success, output = run_command(["nonexistent_command_xyz"])
        assert success is False
        assert "not found" in output.lower()


class TestFileExistence:
    """Tests for file existence checking."""

    def test_check_file_exists_real_file(self) -> None:
        """Test checking existence of a real file."""
        # This test runs from deploy/providers/AWS, so Makefile should exist
        result = check_file_exists("Makefile", "Makefile")
        assert result is True

    def test_check_file_exists_missing_file(self) -> None:
        """Test checking existence of a non-existent file."""
        result = check_file_exists("nonexistent_file_xyz.txt", "Fake file")
        assert result is False


class TestPythonSyntax:
    """Tests for Python syntax checking."""

    def test_python_syntax_valid(self) -> None:
        """Test checking valid Python syntax."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("print('hello')\n")
            f.flush()
            result = check_python_syntax(f.name, "Test script")
            assert result is True
            Path(f.name).unlink()

    def test_python_syntax_invalid(self) -> None:
        """Test checking invalid Python syntax."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("print('hello'  # missing closing quote\n")
            f.flush()
            result = check_python_syntax(f.name, "Invalid script")
            assert result is False
            Path(f.name).unlink()

    def test_python_syntax_file_not_found(self) -> None:
        """Test checking syntax of non-existent file."""
        result = check_python_syntax("nonexistent_xyz.py", "Missing script")
        assert result is False


class TestBashSyntax:
    """Tests for Bash syntax checking."""

    def test_bash_syntax_valid(self) -> None:
        """Test checking valid Bash syntax."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
            f.write("#!/bin/bash\necho 'hello'\n")
            f.flush()
            result = check_bash_syntax(f.name, "Test script")
            assert result is True
            Path(f.name).unlink()

    def test_bash_syntax_invalid(self) -> None:
        """Test checking invalid Bash syntax."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
            f.write("#!/bin/bash\nif true; then echo 'missing fi'\n")
            f.flush()
            result = check_bash_syntax(f.name, "Invalid script")
            assert result is False
            Path(f.name).unlink()

    def test_bash_syntax_file_not_found(self) -> None:
        """Test checking syntax of non-existent file."""
        result = check_bash_syntax("nonexistent_xyz.sh", "Missing script")
        assert result is False


class TestMakefileTargets:
    """Tests for Makefile target checking."""

    def test_makefile_target_exists(self) -> None:
        """Test detecting existing Make target."""
        # Create temp Makefile with a target
        with tempfile.NamedTemporaryFile(mode="w", suffix="Makefile", delete=False, dir=".") as f:
            f.write(".PHONY: test-target\ntest-target:\n\t@echo 'test'\n")
            f.flush()
            original_makefile = Path("Makefile")
            temp_makefile = Path(f.name)

            # Replace Makefile temporarily
            if original_makefile.exists():
                original_makefile.rename("Makefile.bak")

            temp_makefile.rename("Makefile")

            try:
                result = check_makefile_target("test-target", "Test target")
                assert result is True
            finally:
                Path("Makefile").rename(temp_makefile)
                if Path("Makefile.bak").exists():
                    Path("Makefile.bak").rename(original_makefile)

    def test_makefile_target_missing(self) -> None:
        """Test detecting missing Make target."""
        # Create temp Makefile without a specific target
        with tempfile.NamedTemporaryFile(mode="w", suffix="Makefile", delete=False, dir=".") as f:
            f.write(".PHONY: dummy\ndummy:\n\t@echo 'dummy'\n")
            f.flush()
            original_makefile = Path("Makefile")
            temp_makefile = Path(f.name)

            if original_makefile.exists():
                original_makefile.rename("Makefile.bak")

            temp_makefile.rename("Makefile")

            try:
                result = check_makefile_target("nonexistent-target-xyz", "Missing target")
                assert result is False
            finally:
                Path("Makefile").rename(temp_makefile)
                if Path("Makefile.bak").exists():
                    Path("Makefile.bak").rename(original_makefile)


class TestMakefileDependencies:
    """Tests for Makefile dependency checking."""

    def test_makefile_dependency_exists(self) -> None:
        """Test detecting existing Make dependency."""
        with tempfile.NamedTemporaryFile(mode="w", suffix="Makefile", delete=False, dir=".") as f:
            f.write(".PHONY: target1 target2\ntarget1: target2\n\t@echo 'target1'\ntarget2:\n\t@echo 'target2'\n")
            f.flush()
            original_makefile = Path("Makefile")
            temp_makefile = Path(f.name)

            if original_makefile.exists():
                original_makefile.rename("Makefile.bak")

            temp_makefile.rename("Makefile")

            try:
                result = check_makefile_dependency("target1", "target2", "Dependency check")
                assert result is True
            finally:
                Path("Makefile").rename(temp_makefile)
                if Path("Makefile.bak").exists():
                    Path("Makefile.bak").rename(original_makefile)

    def test_makefile_dependency_missing(self) -> None:
        """Test detecting missing Make dependency."""
        with tempfile.NamedTemporaryFile(mode="w", suffix="Makefile", delete=False, dir=".") as f:
            f.write(".PHONY: target1\ntarget1:\n\t@echo 'target1'\n")
            f.flush()
            original_makefile = Path("Makefile")
            temp_makefile = Path(f.name)

            if original_makefile.exists():
                original_makefile.rename("Makefile.bak")

            temp_makefile.rename("Makefile")

            try:
                result = check_makefile_dependency("target1", "nonexistent", "Missing dependency")
                assert result is False
            finally:
                Path("Makefile").rename(temp_makefile)
                if Path("Makefile.bak").exists():
                    Path("Makefile.bak").rename(original_makefile)


class TestIntegration:
    """Integration tests for the verification script."""

    def test_verify_actual_files_exist(self) -> None:
        """Test that actual deployment files exist."""
        # These tests run from deploy/providers/AWS
        assert check_file_exists("update_ssm_ssh_config.py", "SSH config script")
        assert check_file_exists("check_status.py", "Status checker")
        assert check_file_exists("test_update_ssm_ssh_config.py", "Unit tests")
        assert check_file_exists("Makefile", "Makefile")
        assert check_file_exists("site.yml", "Ansible playbook")

    def test_verify_actual_makefile_targets(self) -> None:
        """Test that actual Make targets exist."""
        assert check_makefile_target("ssh-config", "SSH config target")
        assert check_makefile_target("full-deploy", "Full deploy target")
        assert check_makefile_target("deploy-centralhub", "Deploy Central Hub target")

    def test_verify_actual_makefile_dependencies(self) -> None:
        """Test that actual Make dependencies are correct."""
        assert check_makefile_dependency("gen-trust-ec2-certs", "ssh-config", "Cert gen depends on ssh-config")
