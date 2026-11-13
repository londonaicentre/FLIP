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

"""Tests for the deploy readiness verification script."""

from pathlib import Path

import pytest

from verify_deploy_readiness import (
    check_bash_syntax,
    check_file_exists,
    check_makefile_dependency,
    check_makefile_dependency_absent,
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


@pytest.mark.parametrize(
    ("check", "name", "content", "expected"),
    [
        (check_python_syntax, "ok.py", "print('hello')\n", True),
        (check_python_syntax, "bad.py", "print('hello'  # missing closing quote\n", False),
        (check_python_syntax, "missing.py", None, False),
        (check_bash_syntax, "ok.sh", "#!/bin/bash\necho 'hello'\n", True),
        (check_bash_syntax, "bad.sh", "#!/bin/bash\nif true; then echo 'missing fi'\n", False),
        (check_bash_syntax, "missing.sh", None, False),
    ],
    ids=["python-valid", "python-invalid", "python-missing", "bash-valid", "bash-invalid", "bash-missing"],
)
def test_syntax_check(tmp_path: Path, check, name: str, content: str | None, expected: bool) -> None:
    """The Python and Bash syntax checks accept a valid script and reject an invalid or missing one."""
    path = tmp_path / name
    if content is not None:
        path.write_text(content)
    assert check(str(path), f"Test script {name}") is expected


class TestMakefileTargets:
    """Tests for Makefile target checking."""

    def test_makefile_target_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test detecting existing Make target."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: test-target\ntest-target:\n\t@echo 'test'\n")
        monkeypatch.chdir(tmp_path)
        result = check_makefile_target("test-target", "Test target")
        assert result is True

    def test_makefile_target_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test detecting missing Make target."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: dummy\ndummy:\n\t@echo 'dummy'\n")
        monkeypatch.chdir(tmp_path)
        result = check_makefile_target("nonexistent-target-xyz", "Missing target")
        assert result is False


class TestMakefileDependencies:
    """Tests for Makefile dependency checking."""

    def test_makefile_dependency_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test detecting existing Make dependency."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            ".PHONY: target1 target2\ntarget1: target2\n\t@echo 'target1'\ntarget2:\n\t@echo 'target2'\n"
        )
        monkeypatch.chdir(tmp_path)
        result = check_makefile_dependency("target1", "target2", "Dependency check")
        assert result is True

    def test_makefile_dependency_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test detecting missing Make dependency."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: target1\ntarget1:\n\t@echo 'target1'\n")
        monkeypatch.chdir(tmp_path)
        result = check_makefile_dependency("target1", "nonexistent", "Missing dependency")
        assert result is False

    def test_makefile_dependency_absent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test accepting an intentionally absent Make dependency."""
        makefile = tmp_path / "Makefile"
        makefile.write_text(".PHONY: target1\ntarget1:\n\t@echo 'target1'\n")
        monkeypatch.chdir(tmp_path)
        result = check_makefile_dependency_absent("target1", "obsolete", "Obsolete dependency")
        assert result is True


class TestIntegration:
    """Integration tests for the verification script."""

    def test_verify_actual_files_exist(self) -> None:
        """Test that actual deployment files exist."""
        # These tests run from deploy/providers/AWS
        assert check_file_exists("update_ssm_ssh_config.py", "SSH config script")
        assert check_file_exists("check_status.py", "Status checker")
        assert check_file_exists("tests/test_update_ssm_ssh_config.py", "Unit tests")
        assert check_file_exists("Makefile", "Makefile")
        assert check_file_exists("site.yml", "Ansible playbook")

    def test_verify_actual_makefile_targets(self) -> None:
        """Test that actual Make targets exist."""
        assert check_makefile_target("ssh-config", "SSH config target")
        assert check_makefile_target("full-deploy", "Full deploy target")
        assert check_makefile_target("deploy-centralhub", "Deploy Central Hub target")

    def test_verify_actual_makefile_dependencies(self) -> None:
        """Test that actual Make dependencies are correct."""
        assert check_makefile_dependency_absent(
            "deploy-centralhub", "ssh-config", "Deploy Central Hub does not depend on SSH config"
        )
        assert check_makefile_dependency("full-deploy", "ansible-init", "Full deploy configures the bastion")
