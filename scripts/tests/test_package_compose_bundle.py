#!/usr/bin/env python3
# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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
"""Tests for the compose bundle (FLIP#1753).

The load-bearing ones are the last class: the bundle is unpacked into a directory with no ``.git``
and asked to parse the upgrade verb's Makefile chain. Everything above it is a detail; that is the
question the artifact exists to answer.

Usage:
    python3 scripts/tests/test_package_compose_bundle.py
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import package_compose_bundle as bundle  # noqa: E402

MANIFEST_TEXT = """
Makefile
deploy/env_mode.mk
trust/deploy/scripts/
!trust/deploy/scripts/tests
!trust/deploy/scripts/__pycache__
"""


@contextlib.contextmanager
def _expect(exc: type[BaseException], pattern: str):
    """The error a refusal raises, and what it says — ``assertRaisesRegex`` without the unittest spelling."""
    message = None
    try:
        yield
    except exc as caught:
        message = str(caught)
    if message is None:
        raise AssertionError(f"{exc.__name__} was not raised")
    assert re.search(pattern, message), f"{message!r} does not mention {pattern!r}"


def _repo(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp()) / "repo"
    root.mkdir()
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root


class TestManifest(unittest.TestCase):
    def test_a_directory_entry_takes_everything_beneath_it(self):
        root = _repo(
            {
                "Makefile": "x",
                "deploy/env_mode.mk": "x",
                "trust/deploy/scripts/a.py": "x",
                "trust/deploy/scripts/d/b.py": "x",
            }
        )
        assert bundle.select_files(MANIFEST_TEXT, root) == [
            "Makefile",
            "deploy/env_mode.mk",
            "trust/deploy/scripts/a.py",
            "trust/deploy/scripts/d/b.py",
        ]

    def test_an_exclusion_removes_a_directory_and_its_contents(self):
        root = _repo(
            {
                "Makefile": "x",
                "deploy/env_mode.mk": "x",
                "trust/deploy/scripts/a.py": "x",
                "trust/deploy/scripts/tests/t.py": "x",
            }
        )
        assert bundle.select_files(MANIFEST_TEXT, root) == [
            "Makefile",
            "deploy/env_mode.mk",
            "trust/deploy/scripts/a.py",
        ]

    def test_a_pattern_that_matches_nothing_fails_the_build(self):
        """A rename must break packaging, not quietly drop a file from the artifact."""
        root = _repo({"Makefile": "x", "deploy/env_mode.mk": "x"})
        with _expect(bundle.BundleError, "matches no tracked file"):
            bundle.select_files(MANIFEST_TEXT, root)

    def test_a_glob_does_not_cross_a_directory_boundary(self):
        root = _repo({"deploy/compose.prod.yml": "x", "deploy/nested/compose.prod.yml": "x"})
        assert bundle.select_files("deploy/*.yml", root) == ["deploy/compose.prod.yml"]

    def test_a_double_star_does_cross_directories(self):
        root = _repo({"deploy/compose.prod.yml": "x", "deploy/nested/compose.prod.yml": "x"})
        assert bundle.select_files("deploy/**/*.yml", root) == [
            "deploy/compose.prod.yml",
            "deploy/nested/compose.prod.yml",
        ]

    def test_a_kit_file_is_refused(self):
        root = _repo({"Makefile": "x", "trust/.env.GSTT.prod": "TRUST_API_KEY=secret"})
        with _expect(bundle.BundleError, "environment files"):
            bundle.select_files("trust/", root)

    def test_an_empty_manifest_is_refused(self):
        with _expect(bundle.BundleError, "lists nothing"):
            bundle.read_manifest("# nothing here\n\n")


class TestVersion(unittest.TestCase):
    def test_both_spellings_normalise_to_the_tag(self):
        assert bundle.normalise_version("0.13.0") == "v0.13.0"
        assert bundle.normalise_version("v0.13.0") == "v0.13.0"

    def test_a_prerelease_keeps_its_suffix(self):
        assert bundle.normalise_version("v0.13.0-rc.1") == "v0.13.0-rc.1"

    def test_something_that_is_not_a_version_is_refused(self):
        with _expect(bundle.BundleError, "not a release version"):
            bundle.normalise_version("develop")


class TestBuild(unittest.TestCase):
    FILES = {
        "Makefile": "upgrade-onprem-trust:\n\t@echo upgrade\n",
        "deploy/env_mode.mk": "ENV ?= dev\n",
        "trust/deploy/scripts/a.py": "print('a')\n",
    }

    def _build(self, tmp: Path, version: str = "v0.13.0", epoch: int = 1_700_000_000) -> Path:
        root = _repo(self.FILES)
        manifest = tmp / "m"
        manifest.write_text("Makefile\ndeploy/env_mode.mk\ntrust/deploy/scripts/")
        return bundle.build(version, tmp / "out", root, manifest, epoch=epoch)

    def test_it_writes_one_tarball_named_for_the_release(self):
        tmp = Path(tempfile.mkdtemp())
        path = self._build(tmp)
        assert path.name == "flip-compose-bundle-v0.13.0.tar.gz"
        assert path.exists()
        shutil.rmtree(tmp, ignore_errors=True)

    def test_the_tarball_carries_a_manifest_of_itself(self):
        tmp = Path(tempfile.mkdtemp())
        path = self._build(tmp)
        with tarfile.open(path) as tar:
            payload = json.loads(tar.extractfile("BUNDLE.json").read())
        assert payload["release"] == "v0.13.0"
        assert payload["files"] == ["Makefile", "deploy/env_mode.mk", "trust/deploy/scripts/a.py"]
        assert len(payload["commit"]) == 40
        shutil.rmtree(tmp, ignore_errors=True)

    def test_two_builds_of_one_commit_are_byte_identical(self):
        """A trust verifies a published checksum, so the same commit has to package the same bytes."""
        tmp = Path(tempfile.mkdtemp())
        root = _repo(self.FILES)
        manifest = tmp / "m"
        manifest.write_text("Makefile\ndeploy/env_mode.mk\ntrust/deploy/scripts/")
        first = bundle.build("v0.13.0", tmp / "a", root, manifest, epoch=1_700_000_000)
        second = bundle.build("v0.13.0", tmp / "b", root, manifest, epoch=1_700_000_000)
        assert bundle.sha256(first) == bundle.sha256(second)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_the_checksum_file_sits_beside_the_tarball_under_its_full_name(self):
        """`sha256sum -c` looks for `<name>.sha256`; `Path.with_suffix` would produce `<stem>.sha256`."""
        tmp = Path(tempfile.mkdtemp())
        path = self._build(tmp)
        assert bundle.checksum_for(path).name == "flip-compose-bundle-v0.13.0.tar.gz.sha256"
        shutil.rmtree(tmp, ignore_errors=True)

    def test_the_checksum_line_verifies_with_sha256sum(self):
        """The published pair has to work with the tool an operator will actually run."""
        tmp = Path(tempfile.mkdtemp())
        path = self._build(tmp)
        digests = bundle.sha256(path)
        bundle.checksum_for(path).write_text(f"{digests}  {path.name}\n")
        checked = subprocess.run(
            ["sha256sum", "-c", bundle.checksum_for(path).name], cwd=path.parent, capture_output=True, text=True
        )
        assert checked.returncode == 0, checked.stdout + checked.stderr
        shutil.rmtree(tmp, ignore_errors=True)

    def test_a_different_epoch_gives_a_different_checksum(self):
        """Guard the guard: the reproducibility test would pass on a builder that ignored stamps."""
        tmp = Path(tempfile.mkdtemp())
        root = _repo(self.FILES)
        manifest = tmp / "m"
        manifest.write_text("Makefile\n")
        first = bundle.build("v0.13.0", tmp / "a", root, manifest, epoch=1)
        second = bundle.build("v0.13.0", tmp / "b", root, manifest, epoch=2)
        assert bundle.sha256(first) != bundle.sha256(second)
        shutil.rmtree(tmp, ignore_errors=True)


class TestTheRealManifest(unittest.TestCase):
    """Against this repository, not a fixture: the manifest has to fit the tree it ships."""

    @classmethod
    def setUpClass(cls):
        cls.files = bundle.select_files(bundle.MANIFEST.read_text())

    def test_it_selects_the_makefile_chain_the_upgrade_verb_parses(self):
        for required in ("Makefile", "deploy/env_mode.mk", "deploy/fl_backend.mk", "trust/Makefile"):
            assert required in self.files, f"{required} is needed by the upgrade verb's `make` parse"

    def test_it_selects_the_scripts_the_verb_calls(self):
        for required in (
            "trust/deploy/scripts/site_upgrade.py",
            "trust/deploy/scripts/onboard_onprem_trust.py",
            "trust/deploy/scripts/trust_kit_lib.py",
            "scripts/trust_upgrade_agent.py",
        ):
            assert required in self.files, f"{required} is called by the upgrade path"

    def test_it_ships_no_kit_file_and_no_private_key(self):
        assert [f for f in self.files if Path(f).name.startswith(".env")] == []
        assert [f for f in self.files if f.endswith((".pem", ".key"))] == []

    def test_it_does_not_ship_the_hub_or_the_frontend(self):
        """The bundle is what a trust runs, not the platform: 100 MB of Vue is not part of it."""
        for absent in ("flip-api", "flip-ui", "fl-tutorials", "docs"):
            assert [f for f in self.files if f.startswith(f"{absent}/")] == [], f"{absent} is not trust-side"

    def test_it_does_not_ship_the_helm_chart_or_terraform(self):
        """Those are the other deployment shapes; a compose trust has no use for them."""
        assert [f for f in self.files if f.startswith("trust/deploy/helm/")] == []
        assert [f for f in self.files if f.startswith("trust/deploy/terraform/")] == []

    def test_the_bundle_is_small_enough_to_download_at_a_site(self):
        """A guard on the manifest's breadth: the whole point is a small artifact, not a tarball repo."""
        tmp = Path(tempfile.mkdtemp())
        path = bundle.build("v0.0.0", tmp, epoch=1_700_000_000)
        size = path.stat().st_size
        assert size < 5 * 1024 * 1024, f"{size} bytes is not a bundle"
        shutil.rmtree(tmp, ignore_errors=True)


class TestTheBundleCanRunTheUpgrade(unittest.TestCase):
    """The question the artifact exists to answer: unpacked with no `.git`, does the verb still parse?"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.path = bundle.build("v0.0.0", cls.tmp / "out", epoch=1_700_000_000)
        cls.site = cls.tmp / "site"
        cls.site.mkdir()
        with tarfile.open(cls.path) as tar:
            tar.extractall(cls.site)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_make_parses_the_upgrade_verb_inside_the_unpacked_bundle(self):
        parsed = subprocess.run(
            ["make", "-n", "upgrade-onprem-trust", "KIT=GSTT"],
            cwd=self.site,
            capture_output=True,
            timeout=120,
        )
        assert parsed.returncode == 0, (
            "the bundle cannot parse the verb it exists to run:\n"
            f"{parsed.stdout.decode()[-2000:]}\n{parsed.stderr.decode()[-2000:]}"
        )

    def test_the_unpacked_bundle_has_no_git_directory(self):
        """`site_upgrade.py` must not be able to think it is in a checkout."""
        assert not (self.site / ".git").exists()
        assert (self.site / "Makefile").exists()


if __name__ == "__main__":
    unittest.main(verbosity=2)
