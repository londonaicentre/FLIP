#!/usr/bin/env -S uv run --script
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
"""Tests for scripts/site_upgrade.py — the operator-side release resolver (FLIP#1204).

Covers the decisions the script makes before any container moves: which tag is the
target (CLI, else the hub's running build), what counts as an image tag, when a move is a
downgrade, and that writing the tag into the kit touches exactly the two Hub-shared keys.

Usage:
    uv run scripts/tests/test_site_upgrade.py
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "site_upgrade.py"


def _load():
    spec = importlib.util.spec_from_file_location("site_upgrade", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


su = _load()

KIT_TEMPLATE = """# ── Host-local profile ──
OMOP_DB_PORT=5434
# ── Hub-shared (managed by register-trust / sync-trust-kits — do not edit) ──
AES_KEY_BASE64=abc=
CENTRAL_HUB_API_URL=https://hub.example/api
DOCKER_TAG={tag}
DOCKER_REGISTRY=ghcr.io/londonaicentre/
DOCKER_FL_TAG={fl_tag}
DOCKER_FL_REGISTRY=ghcr.io/londonaicentre/
# ── Kit credentials (managed) ──
TRUST_API_KEY=secret
"""


def _write_kit(tmp: Path, tag: str = "sha-badcff1", fl_tag: str = "sha-badcff1") -> Path:
    kit = tmp / ".env.AICP.production"
    kit.write_text(KIT_TEMPLATE.format(tag=tag, fl_tag=fl_tag))
    os.chmod(kit, 0o600)
    return kit


def _needs_tag(fn, *args) -> str:
    """Call ``fn`` and return the NeedsTag message it raises (fail if it does not raise)."""
    try:
        fn(*args)
    except su.NeedsTag as e:
        return str(e)
    raise AssertionError("NeedsTag not raised")


class ImageTagShape(unittest.TestCase):
    def test_release_and_sha_tags_are_image_tags(self):
        for tag in ("v0.6.0", "v10.2.13", "v0.6.1-rc.1", "sha-badcff1", "sha-0123abc"):
            with self.subTest(tag=tag):
                assert su.is_image_tag(tag)

    def test_pyproject_versions_and_floating_tags_are_not(self):
        """0.6.0 is what a hub built without FLIP_RELEASE reports — a version, not a pullable pin;
        prod/stag/latest move under a site and defeat the whole point."""
        for tag in ("0.6.0", "prod", "stag", "latest", "", "v0.6", "sha-badcff", "arkplus-platform-on-505"):
            with self.subTest(tag=tag):
                assert not su.is_image_tag(tag)


class Downgrade(unittest.TestCase):
    def test_lower_release_is_a_downgrade(self):
        assert su.is_downgrade("v0.6.0", "v0.5.0")
        assert su.is_downgrade("v1.0.0", "v0.9.9")

    def test_same_or_higher_release_is_not(self):
        assert not su.is_downgrade("v0.5.0", "v0.6.0")
        assert not su.is_downgrade("v0.6.0", "v0.6.0")

    def test_sha_tags_are_never_decidable(self):
        """Two shas have no order; neither does sha→release. Only a release→release move can say."""
        assert not su.is_downgrade("sha-badcff1", "v0.6.0")
        assert not su.is_downgrade("v0.6.0", "sha-badcff1")
        assert not su.is_downgrade("arkplus-platform-on-505", "v0.5.0")


class ResolveTarget(unittest.TestCase):
    def test_cli_tag_wins_without_asking_the_hub(self):
        with mock.patch.object(su, "fetch_hub_version", side_effect=AssertionError("must not be called")):
            assert su.resolve_target("v0.6.0", "https://hub.example/api") == ("v0.6.0", "cli")

    def test_cli_tag_must_be_an_image_tag(self):
        assert "not an image tag" in _needs_tag(su.resolve_target, "prod", "https://hub.example/api")

    def test_defaults_to_the_hubs_running_build(self):
        with mock.patch.object(su, "fetch_hub_version", return_value="v0.6.0"):
            assert su.resolve_target(None, "https://hub.example/api") == ("v0.6.0", "hub")

    def test_a_hub_that_reports_a_version_number_not_an_image_tag_needs_an_explicit_tag(self):
        """A hub built before FLIP#1204 says 0.6.0 (pyproject) or nothing at all."""
        for reported in ("0.6.0", "unknown", None):
            with self.subTest(reported=reported), mock.patch.object(su, "fetch_hub_version", return_value=reported):
                assert "TAG=" in _needs_tag(su.resolve_target, None, "https://hub.example/api")

    def test_an_unreachable_hub_needs_an_explicit_tag(self):
        with mock.patch.object(su, "fetch_hub_version", side_effect=urllib.error.URLError("refused")):
            assert "TAG=" in _needs_tag(su.resolve_target, None, "https://hub.example/api")


class FetchHubVersion(unittest.TestCase):
    def test_reads_version_from_the_health_body(self):
        body = io.BytesIO(json.dumps({"status": "ok", "version": "v0.6.0"}).encode())
        with mock.patch.object(su.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = body
            assert su.fetch_hub_version("https://hub.example/api") == "v0.6.0"
            assert urlopen.call_args[0][0].full_url == "https://hub.example/api/health"

    def test_tolerates_a_body_without_version(self):
        body = io.BytesIO(json.dumps({"status": "ok"}).encode())
        with mock.patch.object(su.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = body
            assert su.fetch_hub_version("https://hub.example/api") is None


class WriteTags(unittest.TestCase):
    def test_rewrites_exactly_the_two_hub_shared_tag_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            su.write_tags(kit, "v0.6.0")
            text = kit.read_text()
        assert "DOCKER_TAG=v0.6.0\n" in text
        assert "DOCKER_FL_TAG=v0.6.0\n" in text
        assert text.count("DOCKER_TAG=") == 1
        # Everything else — comments, ordering, the other keys — is byte-identical.
        assert text == KIT_TEMPLATE.format(tag="v0.6.0", fl_tag="v0.6.0")

    def test_keeps_the_kit_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            su.write_tags(kit, "v0.6.0")
            assert stat.S_IMODE(kit.stat().st_mode) == 0o600

    def test_read_current_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp), tag="sha-badcff1")
            assert su.read_kit(kit)["DOCKER_TAG"] == "sha-badcff1"
            assert su.read_kit(kit)["CENTRAL_HUB_API_URL"] == "https://hub.example/api"


class SiteImages(unittest.TestCase):
    """The registry preflight covers exactly what the compose files pull at the kit's tags."""

    def test_lists_every_repo_built_image_at_the_target_tag(self):
        refs = su.site_images({"DOCKER_REGISTRY": "ghcr.io/londonaicentre/"}, "v0.6.1")
        names = {ref.rsplit("/", 1)[1] for ref in refs}
        assert names == {
            "trust-api:v0.6.1",
            "imaging-api:v0.6.1",
            "data-access-api:v0.6.1",
            "orthanc:v0.6.1",
            "xnat-web:v0.6.1",
            "xnat-db:v0.6.1",
            "xnat-nginx:v0.6.1",
            "omop-db:v0.6.1",
            "flare-fl-client:v0.6.1",
        }
        assert all(ref.startswith("ghcr.io/londonaicentre/") for ref in refs)

    def test_flower_sites_check_the_supernode_and_a_pinned_omop_db_is_left_alone(self):
        refs = su.site_images({"FL_BACKEND": "flower", "OMOP_DB_TAG": "latest"}, "v0.6.1")
        names = {ref.rsplit("/", 1)[1] for ref in refs}
        assert "flower-supernode:v0.6.1" in names
        assert "flare-fl-client:v0.6.1" not in names
        assert not any(name.startswith("omop-db:") for name in names)

    def test_missing_images_are_the_ones_the_registry_does_not_serve(self):
        def probe(ref: str, timeout: float = 0) -> bool:
            return "orthanc" not in ref

        with mock.patch.object(su, "manifest_exists", side_effect=probe):
            assert su.missing_images({}, "sha-badcff1") == ["ghcr.io/londonaicentre/orthanc:sha-badcff1"]

    def test_manifest_probe_is_the_docker_exit_code(self):
        with mock.patch.object(su.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            assert su.manifest_exists("ghcr.io/x/y:v1")
        assert run.call_args.args[0] == ["docker", "manifest", "inspect", "ghcr.io/x/y:v1"]
        with mock.patch.object(su.subprocess, "run", return_value=mock.Mock(returncode=1)):
            assert not su.manifest_exists("ghcr.io/x/y:v1")
        with mock.patch.object(su.subprocess, "run", side_effect=OSError("no docker")):
            assert not su.manifest_exists("ghcr.io/x/y:v1")


class Plan(unittest.TestCase):
    """End-to-end through main(): exit codes are the Makefile's contract."""

    def setUp(self):
        # Every image is published unless a test says otherwise — never probe a registry from a unit test.
        patcher = mock.patch.object(su, "missing_images", return_value=[])
        self.missing_images = patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, kit: Path, *argv: str, stdin: str = "") -> tuple[int, str]:
        out = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", io.StringIO(stdin)),
            mock.patch.object(sys, "stdout", out),
            mock.patch.object(sys, "argv", ["site_upgrade.py", "plan", "--kit-file", str(kit), *argv]),
        ):
            try:
                su.main()
            except SystemExit as e:
                return int(e.code or 0), out.getvalue()
        return 0, out.getvalue()

    def test_plan_with_yes_writes_and_reports_the_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp), tag="v0.5.0", fl_tag="v0.5.0")
            with mock.patch.object(su, "fetch_hub_version", return_value="v0.6.0"):
                code, out = self._run(kit, "--yes")
            assert code == 0, out
            assert "site v0.5.0 → target v0.6.0 (from the hub)" in out
            assert "DOCKER_TAG=v0.6.0" in kit.read_text()

    def test_plan_refuses_a_downgrade_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp), tag="v0.6.0", fl_tag="v0.6.0")
            code, out = self._run(kit, "--tag", "v0.5.0", "--yes")
            assert code == 3, out
            assert "FORCE=1" in out
            assert "DOCKER_TAG=v0.6.0" in kit.read_text()
            code, _ = self._run(kit, "--tag", "v0.5.0", "--yes", "--force")
            assert code == 0
            assert "DOCKER_TAG=v0.5.0" in kit.read_text()

    def test_plan_needs_a_tag_when_the_hub_cannot_say(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            with mock.patch.object(su, "fetch_hub_version", return_value="0.6.0"):
                code, out = self._run(kit, "--yes")
            assert code == 2, out
            assert "TAG=" in out

    def test_plan_asks_before_writing_and_honours_no(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            with mock.patch.object(sys.stdin, "isatty", return_value=True, create=True):
                code, out = self._run(kit, "--tag", "v0.6.0", stdin="n\n")
            assert code == 4, out
            assert "DOCKER_TAG=sha-badcff1" in kit.read_text()

    def test_plan_without_a_tty_needs_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            code, out = self._run(kit, "--tag", "v0.6.0")
            assert code == 4, out
            assert "YES=1" in out

    def test_dry_run_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            code, out = self._run(kit, "--tag", "v0.6.0", "--dry-run")
            assert code == 0, out
            assert "dry run" in out
            assert "DOCKER_TAG=sha-badcff1" in kit.read_text()

    def test_plan_refuses_a_tag_some_image_was_never_built_at(self):
        # orthanc only rebuilds when trust/orthanc/** changes, so most sha- tags have no orthanc image:
        # refuse before the kit is rewritten, naming the reference, so the pull never fails half-way.
        self.missing_images.return_value = ["ghcr.io/londonaicentre/orthanc:sha-1234567"]
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            code, out = self._run(kit, "--tag", "sha-1234567", "--yes")
            assert code == 5, out
            assert "ghcr.io/londonaicentre/orthanc:sha-1234567" in out
            assert "DOCKER_TAG=sha-badcff1" in kit.read_text()
        self.missing_images.assert_called_once()
        assert self.missing_images.call_args.args[1] == "sha-1234567"

    def test_plan_checks_the_registry_before_asking_the_operator(self):
        self.missing_images.return_value = ["ghcr.io/londonaicentre/xnat-web:v0.6.0"]
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            with mock.patch.object(sys.stdin, "isatty", return_value=True, create=True):
                code, out = self._run(kit, "--tag", "v0.6.0", stdin="y\n")
            assert code == 5, out
            assert "Proceed?" not in out

    def test_plan_without_a_docker_cli_warns_and_leaves_it_to_the_pull(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            with mock.patch.object(su.shutil, "which", return_value=None):
                code, out = self._run(kit, "--tag", "v0.6.0", "--yes")
            assert code == 0, out
            assert "skipping the registry check" in out
            assert "DOCKER_TAG=v0.6.0" in kit.read_text()
        self.missing_images.assert_not_called()


if __name__ == "__main__":
    unittest.main()
