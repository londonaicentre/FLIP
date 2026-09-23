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


def _raised(exc_type: type[BaseException], fn, *args) -> BaseException:
    """Call ``fn`` and return the ``exc_type`` it raises (fail if it does not raise)."""
    try:
        fn(*args)
    except exc_type as e:
        return e
    raise AssertionError(f"{exc_type.__name__} not raised")


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

    def test_a_release_back_to_its_own_pre_release_is_a_downgrade(self):
        """Semver precedence: v0.7.0-rc.1 < v0.7.0, and pre-release numbers compare as numbers."""
        assert su.is_downgrade("v0.7.0", "v0.7.0-rc.1")
        assert su.is_downgrade("v0.7.0-rc.2", "v0.7.0-rc.1")
        assert su.is_downgrade("v0.7.0-rc.10", "v0.7.0-rc.2")
        assert not su.is_downgrade("v0.7.0-rc.1", "v0.7.0")
        assert not su.is_downgrade("v0.7.0-rc.1", "v0.7.0-rc.2")
        assert not su.is_downgrade("v0.6.0", "v0.7.0-rc.1")

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

    def test_flower_sites_check_the_supernode(self):
        names = {ref.rsplit("/", 1)[1] for ref in su.site_images({"FL_BACKEND": "flower"}, "v0.6.1")}
        assert "flower-supernode:v0.6.1" in names
        assert "flare-fl-client:v0.6.1" not in names

    def test_the_fl_client_is_checked_at_its_own_tag_when_one_is_given(self):
        names = {ref.rsplit("/", 1)[1] for ref in su.site_images({}, "sha-1234567", "sha-03fdb61")}
        assert "flare-fl-client:sha-03fdb61" in names
        assert "trust-api:sha-1234567" in names

    def test_a_separately_pinned_image_is_checked_at_its_pin(self):
        kit = {"OMOP_DB_TAG": "latest", "ORTHANC_TAG": "sha-2bf07b9", "XNAT_TAG": "v0.6.0"}
        names = {ref.rsplit("/", 1)[1] for ref in su.site_images(kit, "v0.6.1")}
        pinned = {"omop-db:latest", "orthanc:sha-2bf07b9", "xnat-web:v0.6.0", "xnat-db:v0.6.0", "xnat-nginx:v0.6.0"}
        assert pinned <= names
        assert not any(name in {"omop-db:v0.6.1", "orthanc:v0.6.1", "xnat-web:v0.6.1"} for name in names)
        assert "trust-api:v0.6.1" in names

    def test_missing_images_are_the_ones_the_registry_does_not_serve(self):
        def probe(ref: str, timeout: float = 0) -> bool:
            return "orthanc" not in ref

        with mock.patch.object(su, "manifest_exists", side_effect=probe):
            assert su.missing_images({}, "sha-badcff1") == ["ghcr.io/londonaicentre/orthanc:sha-badcff1"]

    def test_manifest_probe_reads_absence_only_from_the_registrys_answer(self):
        ok = mock.Mock(returncode=0, stdout="{}", stderr="")
        with mock.patch.object(su.subprocess, "run", return_value=ok) as run:
            assert su.manifest_exists("ghcr.io/x/y:v1")
        assert run.call_args.args[0] == ["docker", "manifest", "inspect", "ghcr.io/x/y:v1"]
        absent = mock.Mock(returncode=1, stdout="", stderr="manifest unknown\n")
        with mock.patch.object(su.subprocess, "run", return_value=absent):
            assert not su.manifest_exists("ghcr.io/x/y:v1")

    def test_a_probe_that_fails_for_another_reason_is_not_reported_as_absent(self):
        """A login, network or rate-limit fault must not send the operator after a different tag."""
        cases = {
            "unauthorized": mock.Mock(returncode=1, stdout="", stderr="unauthorized: authentication required"),
            "rate limit": mock.Mock(returncode=1, stdout="", stderr="toomanyrequests: rate limit exceeded"),
        }
        for name, result in cases.items():
            with self.subTest(case=name), mock.patch.object(su.subprocess, "run", return_value=result):
                raised = _raised(su.RegistryUnavailable, su.manifest_exists, "ghcr.io/x/y:v1")
                assert result.stderr in str(raised)
        for exc in (OSError("no docker"), su.subprocess.TimeoutExpired(["docker"], 60)):
            with self.subTest(case=type(exc).__name__), mock.patch.object(su.subprocess, "run", side_effect=exc):
                _raised(su.RegistryUnavailable, su.manifest_exists, "ghcr.io/x/y:v1")


class CheckoutTags(unittest.TestCase):
    """The checkout probe: the tags on HEAD, None outside a checkout, an error when git cannot say."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        (self.repo / ".git").mkdir()

    def test_lists_every_tag_on_head(self):
        out = mock.Mock(returncode=0, stdout="v0.7.0\nrelease-2026\n")
        with mock.patch.object(su.subprocess, "run", return_value=out) as run:
            assert su.checkout_tags(self.repo) == ["v0.7.0", "release-2026"]
        assert run.call_args.args[0] == ["git", "-C", str(self.repo), "tag", "--points-at", "HEAD"]

    def test_an_untagged_head_is_an_empty_list_not_none(self):
        with mock.patch.object(su.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="")):
            assert su.checkout_tags(self.repo) == []

    def test_a_tree_without_git_is_not_a_checkout(self):
        """An unpacked archive: nothing to compare, and git is never asked."""
        with mock.patch.object(su.subprocess, "run", side_effect=AssertionError("must not run git")):
            assert su.checkout_tags(self.repo / "unpacked") is None

    def test_a_checkout_git_cannot_read_is_an_error_not_a_missing_checkout(self):
        """'dubious ownership' (a checkout owned by another user, run under sudo) must stop the guard,
        not pass as "not a checkout" and let an old tree run the new images."""
        refused = mock.Mock(returncode=128, stdout="", stderr="fatal: detected dubious ownership in repository")
        with mock.patch.object(su.subprocess, "run", return_value=refused):
            assert "dubious ownership" in str(_raised(su.CheckoutUnknown, su.checkout_tags, self.repo))
        with mock.patch.object(su.subprocess, "run", side_effect=OSError("no git")):
            _raised(su.CheckoutUnknown, su.checkout_tags, self.repo)

    def test_release_for_sha_maps_the_hubs_build_of_this_checkouts_release(self):
        """A CI-applied hub runs sha-<short7> of the release commit; from that commit's tag, target the release."""

        def git(args, **_kw):
            if args[3:] == ["tag", "--points-at", "HEAD"]:
                return mock.Mock(returncode=0, stdout="v0.7.0-rc.1\nv0.7.0\nflip-utils-v0.5.0\n", stderr="")
            return mock.Mock(returncode=0, stdout="23cf33134b8e2f0\n", stderr="")  # pragma: allowlist secret

        with mock.patch.object(su.subprocess, "run", side_effect=git):
            assert su.release_for_sha("sha-23cf331", self.repo) == "v0.7.0"
            assert su.release_for_sha("sha-1111111", self.repo) is None  # the hub runs another commit
            assert su.release_for_sha("v0.7.0", self.repo) is None
        assert su.release_for_sha("sha-23cf331", self.repo / "unpacked") is None

    def test_describe_names_only_platform_releases(self):
        """The repo carries component tags (flip-utils-v0.5.0) and one-offs; an unmatched describe
        picks the nearest of ANY of them, so the refusal would name a version that is not the
        platform's. --match keeps both sides of "checkout X, target Y" on one scale."""
        out = mock.Mock(returncode=0, stdout="v0.6.0-12-g1234abc\n")
        with mock.patch.object(su.subprocess, "run", return_value=out) as run:
            assert su.describe_checkout(Path("/repo")) == "v0.6.0-12-g1234abc"
        assert run.call_args.args[0][-2:] == ["--match", "v[0-9]*"]

    def test_describe_falls_back_to_unknown(self):
        out = mock.Mock(returncode=0, stdout="v0.6.0-12-gabc1234\n")
        with mock.patch.object(su.subprocess, "run", return_value=out):
            assert su.describe_checkout(Path("/repo")) == "v0.6.0-12-gabc1234"
        with mock.patch.object(su.subprocess, "run", side_effect=OSError("no git")):
            assert su.describe_checkout(Path("/repo")) == "<unknown>"


class Plan(unittest.TestCase):
    """End-to-end through main(): exit codes are the Makefile's contract."""

    def setUp(self):
        # Every image is published unless a test says otherwise — never probe a registry from a unit test.
        patcher = mock.patch.object(su, "missing_images", return_value=[])
        self.missing_images = patcher.start()
        self.addCleanup(patcher.stop)
        # And the checkout is unknowable (not a git tree) unless a test says otherwise — the
        # warn-only path — so no test here depends on which commit the test runner sits on.
        checkout = mock.patch.object(su, "checkout_tags", return_value=None)
        self.checkout_tags = checkout.start()
        self.addCleanup(checkout.stop)

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

    def test_plan_pins_the_fl_client_apart_with_fl_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            code, out = self._run(kit, "--tag", "sha-1234567", "--fl-tag", "sha-03fdb61", "--yes")
            assert code == 0, out
            assert "FL client sha-badcff1 → sha-03fdb61" in out
            text = kit.read_text()
            assert "DOCKER_TAG=sha-1234567" in text
            assert "DOCKER_FL_TAG=sha-03fdb61" in text  # pragma: allowlist secret
        assert self.missing_images.call_args.args[1:] == ("sha-1234567", "sha-03fdb61")  # pragma: allowlist secret

    def test_plan_rejects_a_floating_fl_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            code, out = self._run(kit, "--tag", "v0.6.0", "--fl-tag", "stag", "--yes")
            assert code == 2, out
            assert "FL_TAG=" in out
            assert "DOCKER_TAG=sha-badcff1" in kit.read_text()

    def test_plan_refuses_a_release_target_from_a_checkout_at_another_tag(self):
        """The verb runs from the tree that carries the compose files; a v0.6.0 tree must not pin v0.7.0."""
        self.checkout_tags.return_value = ["v0.6.0"]
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp), tag="v0.6.0", fl_tag="v0.6.0")
            with mock.patch.object(su, "describe_checkout", return_value="v0.6.0"):
                code, out = self._run(kit, "--tag", "v0.7.0", "--yes")
            assert code == su.EXIT_CHECKOUT_MISMATCH, out
            assert "checkout is v0.6.0, but the target is v0.7.0" in out
            assert "git -C" in out
            assert "checkout v0.7.0" in out
            assert "fetch --tags" in out
            assert "DOCKER_TAG=v0.6.0" in kit.read_text()
        # Refused before the registry is asked: the stale tree is the cheaper fix.
        self.missing_images.assert_not_called()

    def test_plan_accepts_a_release_target_from_the_tagged_checkout(self):
        self.checkout_tags.return_value = ["v0.7.0", "some-other-tag"]
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp), tag="v0.6.0", fl_tag="v0.6.0")
            code, out = self._run(kit, "--tag", "v0.7.0", "--yes")
            assert code == 0, out
            assert "checkout is at v0.7.0" in out
            assert "DOCKER_TAG=v0.7.0" in kit.read_text()

    def test_plan_allows_checkout_drift_only_when_told(self):
        self.checkout_tags.return_value = []
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp), tag="v0.6.0", fl_tag="v0.6.0")
            with mock.patch.object(su, "describe_checkout", return_value="v0.6.0-12-gabc1234"):
                code, out = self._run(kit, "--tag", "v0.7.0", "--yes", "--allow-checkout-drift")
            assert code == 0, out
            assert "drift allowed" in out
            assert "DOCKER_TAG=v0.7.0" in kit.read_text()

    def test_plan_never_checks_the_checkout_for_a_sha_target(self):
        """A sha- tag names a CI build, not a git tag — there is nothing to check out for it."""
        self.checkout_tags.return_value = ["v0.6.0"]
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            code, out = self._run(kit, "--tag", "sha-abcdef0", "--yes")  # pragma: allowlist secret
            assert code == 0, out
            assert "checkout" not in out
        self.checkout_tags.assert_not_called()

    def test_plan_outside_a_git_checkout_warns_and_continues(self):
        self.checkout_tags.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp), tag="v0.6.0", fl_tag="v0.6.0")
            code, out = self._run(kit, "--tag", "v0.7.0", "--yes")
            assert code == 0, out
            assert "not a git checkout" in out

    def test_plan_refuses_a_release_target_when_git_cannot_read_the_checkout(self):
        self.checkout_tags.side_effect = su.CheckoutUnknown("git tag --points-at HEAD: detected dubious ownership")
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp), tag="v0.6.0", fl_tag="v0.6.0")
            code, out = self._run(kit, "--tag", "v0.7.0", "--yes")
            assert code == su.EXIT_CHECKOUT_MISMATCH, out
            assert "dubious ownership" in out
            assert "safe.directory" in out
            assert "DOCKER_TAG=v0.6.0" in kit.read_text()
            code, out = self._run(kit, "--tag", "v0.7.0", "--yes", "--allow-checkout-drift")
            assert code == 0, out
            assert "drift allowed" in out
        self.missing_images.assert_called_once()

    def test_plan_reports_an_unreachable_registry_as_such(self):
        self.missing_images.side_effect = su.RegistryUnavailable(
            "ghcr.io/londonaicentre/trust-api:v0.6.0: unauthorized"
        )
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            code, out = self._run(kit, "--tag", "v0.6.0", "--yes")
            assert code == su.EXIT_MISSING_IMAGES, out
            assert "Could not ask the registry" in out
            assert "unauthorized" in out
            assert "pick a tag" not in out
            assert "DOCKER_TAG=sha-badcff1" in kit.read_text()

    def test_plan_targets_this_checkouts_release_when_the_hub_runs_its_sha_build(self):
        self.checkout_tags.return_value = ["v0.7.0"]
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp), tag="v0.6.0", fl_tag="v0.6.0")
            with (
                mock.patch.object(su, "fetch_hub_version", return_value="sha-23cf331"),
                mock.patch.object(su, "release_for_sha", return_value="v0.7.0") as mapped,
            ):
                code, out = self._run(kit, "--yes")
            assert code == 0, out
            assert "target v0.7.0 (the hub runs sha-23cf331, the build of this checkout's v0.7.0)" in out
            assert "DOCKER_TAG=v0.7.0" in kit.read_text()
        mapped.assert_called_once_with("sha-23cf331")

    def test_plan_never_remaps_an_explicit_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp))
            with mock.patch.object(su, "release_for_sha", side_effect=AssertionError("TAG= is taken as given")):
                code, out = self._run(kit, "--tag", "sha-23cf331", "--yes")
            assert code == 0, out
            assert "DOCKER_TAG=sha-23cf331" in kit.read_text()

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
