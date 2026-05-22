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
"""Unit tests for the supply-chain cooldown gate.

Run with ``python -m unittest discover`` from this directory. The tests use
only the standard library and never touch the network: registry timestamps are
supplied directly to the pure evaluation/reporting functions.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import check_package_age as cpa

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)


class ParseUvLockTests(unittest.TestCase):
    """Parsing of uv lockfiles."""

    def test_extracts_registry_packages_only(self) -> None:
        packages = cpa.parse_uv_lock(FIXTURES / "sample.uv.lock")
        names = {pkg.name for pkg in packages}
        # PyPI-registry sources are checked.
        assert "recent-demo-package" in names
        assert "annotated-types" in names
        # Editable (first-party) and git sources are not.
        assert "flip-sample-app" not in names
        assert "internal-git-tool" not in names
        assert all(pkg.ecosystem == cpa.PYPI for pkg in packages)


class ParsePackageLockTests(unittest.TestCase):
    """Parsing of npm lockfiles."""

    def test_extracts_npm_registry_packages_only(self) -> None:
        packages = cpa.parse_package_lock(FIXTURES / "sample.package-lock.json")
        by_name = {pkg.name: pkg for pkg in packages}
        assert "axios" in by_name
        assert by_name["axios"].version == "1.7.0"
        # Scoped package names are recovered from the lockfile key.
        assert "@scope/recent-demo-package" in by_name
        # Root project, workspace symlinks and git sources are skipped.
        assert "flip-sample-ui" not in by_name
        assert "internal-workspace-lib" not in by_name
        assert "git-sourced-tool" not in by_name
        assert all(pkg.ecosystem == cpa.NPM for pkg in packages)

    def test_name_from_key(self) -> None:
        assert cpa._npm_name_from_key("node_modules/axios") == "axios"
        assert cpa._npm_name_from_key("node_modules/@vue/runtime") == "@vue/runtime"
        assert cpa._npm_name_from_key("node_modules/a/node_modules/b") == "b"
        assert cpa._npm_name_from_key("workspaces/foo") is None


class DiscoverLockfileTests(unittest.TestCase):
    """Lockfile discovery from explicit and directory paths."""

    def test_explicit_file_is_returned(self) -> None:
        found = cpa.discover_lockfiles([str(FIXTURES / "sample.uv.lock")])
        assert found == [FIXTURES / "sample.uv.lock"]

    def test_missing_path_warns_without_raising(self) -> None:
        assert cpa.discover_lockfiles(["does/not/exist/uv.lock"]) == []


class EvaluateTests(unittest.TestCase):
    """Classification of packages against the cooldown window."""

    def _package(self, name: str = "recent-demo-package", version: str = "9.9.9") -> cpa.LockedPackage:
        return cpa.LockedPackage(cpa.PYPI, name, version, "uv.lock")

    def test_recent_release_is_a_violation(self) -> None:
        package = self._package()
        results = {package.cache_key: NOW - timedelta(hours=10)}
        violations, unverified, skipped = cpa.evaluate([package], results, {}, NOW, 72)
        assert len(violations) == 1
        assert abs(violations[0].age_hours - 10.0) < 1e-6
        assert unverified == []
        assert skipped == []

    def test_release_older_than_window_passes(self) -> None:
        package = self._package()
        results = {package.cache_key: NOW - timedelta(hours=200)}
        violations, _, _ = cpa.evaluate([package], results, {}, NOW, 72)
        assert violations == []

    def test_release_exactly_at_window_passes(self) -> None:
        package = self._package()
        results = {package.cache_key: NOW - timedelta(hours=72)}
        violations, _, _ = cpa.evaluate([package], results, {}, NOW, 72)
        assert violations == []

    def test_unverified_package_is_reported(self) -> None:
        package = self._package()
        errors = {package.cache_key: cpa.RegistryError("registry unreachable")}
        violations, unverified, skipped = cpa.evaluate([package], {}, errors, NOW, 72)
        assert violations == []
        assert len(unverified) == 1
        assert skipped == []

    def test_package_absent_from_registry_is_skipped(self) -> None:
        package = self._package()
        violations, unverified, skipped = cpa.evaluate(
            [package], {package.cache_key: None}, {}, NOW, 72
        )
        assert violations == []
        assert unverified == []
        assert skipped == [package]

    def test_fixture_lockfile_with_recent_pin_fails_and_clean_state_passes(self) -> None:
        """The fixture lockfile fails when a pin is recent and passes otherwise.

        Covers the acceptance criterion that the check fails against a fixture
        lockfile pinning a known-recent version, and passes when every pin has
        aged out of the cooldown window.
        """
        packages = cpa.parse_uv_lock(FIXTURES / "sample.uv.lock")
        target = next(pkg for pkg in packages if pkg.name == "recent-demo-package")

        recent = {pkg.cache_key: NOW - timedelta(days=400) for pkg in packages}
        recent[target.cache_key] = NOW - timedelta(hours=6)
        violations, _, _ = cpa.evaluate(packages, recent, {}, NOW, 72)
        assert [v.package.name for v in violations] == ["recent-demo-package"]

        aged_out = {pkg.cache_key: NOW - timedelta(days=400) for pkg in packages}
        violations, _, _ = cpa.evaluate(packages, aged_out, {}, NOW, 72)
        assert violations == []


class OverrideTests(unittest.TestCase):
    """Resolution of the emergency override."""

    def test_label_activates_override(self) -> None:
        active, reason = cpa.resolve_override(True, [])
        assert active
        assert cpa.OVERRIDE_LABEL in reason

    def test_commit_marker_activates_override(self) -> None:
        active, reason = cpa.resolve_override(
            False, ["chore: routine commit", "fix: urgent CVE patch\n\n[skip-cooldown]"]
        )
        assert active
        assert cpa.OVERRIDE_COMMIT_MARKER in reason

    def test_no_override_by_default(self) -> None:
        active, reason = cpa.resolve_override(False, ["chore: a perfectly ordinary commit"])
        assert not active
        assert reason == ""

    def test_parse_bool(self) -> None:
        for truthy in ("true", "True", "TRUE", "1", "yes", "on", " true "):
            assert cpa._parse_bool(truthy), truthy
        for falsy in ("false", "False", "", "0", "no", "off", None):
            assert not cpa._parse_bool(falsy), falsy


class ReportTests(unittest.TestCase):
    """Exit-code behaviour of the reporting stage."""

    def setUp(self) -> None:
        package = cpa.LockedPackage(cpa.PYPI, "recent-demo-package", "9.9.9", "uv.lock")
        self.violation = cpa.Violation(package, NOW - timedelta(hours=5), 5.0)
        self.unverified = (package, cpa.RegistryError("registry unreachable"))

    def test_clean_run_passes(self) -> None:
        code = cpa.report([], [], [], cooldown_hours=72, override=False, override_reason="")
        assert code == 0

    def test_violation_fails_the_build(self) -> None:
        code = cpa.report(
            [self.violation], [], [], cooldown_hours=72, override=False, override_reason=""
        )
        assert code == 1

    def test_unverified_fails_the_build(self) -> None:
        code = cpa.report(
            [], [self.unverified], [], cooldown_hours=72, override=False, override_reason=""
        )
        assert code == 1

    def test_override_allows_violation_through(self) -> None:
        """An active override downgrades a violation to a warning (exit 0)."""
        code = cpa.report(
            [self.violation],
            [],
            [],
            cooldown_hours=72,
            override=True,
            override_reason=f"the '{cpa.OVERRIDE_LABEL}' pull-request label",
        )
        assert code == 0

    def test_override_allows_unverified_through(self) -> None:
        code = cpa.report(
            [],
            [self.unverified],
            [],
            cooldown_hours=72,
            override=True,
            override_reason="the '[skip-cooldown]' marker in a commit message",
        )
        assert code == 0


if __name__ == "__main__":
    unittest.main()
