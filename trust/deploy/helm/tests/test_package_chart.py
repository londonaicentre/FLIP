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
"""The published chart must be immutable in substance, not just in tag (FLIP#1646).

``package_chart.py`` is pure text transformation plus two helm calls, so everything that could be
wrong about a published chart is checked here without a registry, a cluster or helm itself.

The failure this file exists to prevent: a chart published as ``flip-trust:0.13.0`` whose images
still float on ``stag``. It would look versioned, pass every smoke test on the day it shipped, and
then deploy different code next week -- which is precisely the property the upgrade agent is being
built to rely on.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

CHART_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CHART_DIR / "scripts"))

from package_chart import (  # noqa: E402
    DEFAULT_REGISTRY,
    StampError,
    normalise_version,
    publish,
    stamp_chart,
    stamp_values,
)

REAL_CHART = (CHART_DIR / "Chart.yaml").read_text()
REAL_VALUES = (CHART_DIR / "values.yaml").read_text()


class TestVersionNormalisation:
    """Both spellings of a release reach this script; neither may surprise it."""

    def test_the_v_prefix_is_optional_on_the_way_in(self) -> None:
        assert normalise_version("v0.13.0") == ("0.13.0", "v0.13.0")
        assert normalise_version("0.13.0") == ("0.13.0", "v0.13.0")

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        """A version read out of a file or a workflow output arrives with a newline on it."""
        assert normalise_version(" v1.2.3\n") == ("1.2.3", "v1.2.3")

    def test_a_prerelease_is_a_valid_chart_version(self) -> None:
        assert normalise_version("v1.2.3-rc.1") == ("1.2.3-rc.1", "v1.2.3-rc.1")

    def test_a_non_semver_version_is_refused(self) -> None:
        """Helm would reject these too -- but only after the release had tagged and built."""
        for bad in ("stag", "prod", "1.2", "v1.2.3.4", "latest", "", "v", "sha-abc123"):
            with pytest.raises(StampError):
                normalise_version(bad)

    def test_a_version_is_not_matched_loosely(self) -> None:
        """Anchoring matters: `v1.2.3; rm -rf /` must not pass as `1.2.3`."""
        for bad in ("v1.2.3 extra", "prefix v1.2.3", "v1.2.3\nv9.9.9"):
            with pytest.raises(StampError):
                normalise_version(bad)


class TestChartStamping:
    """Chart.yaml carries the release twice: as the chart's SemVer and as what it deploys."""

    def test_the_chart_version_becomes_the_release(self) -> None:
        out = stamp_chart(REAL_CHART, "0.13.0", "v0.13.0")
        assert "\nversion: 0.13.0\n" in out

    def test_the_app_version_is_the_release_tag(self) -> None:
        """appVersion names the thing deployed, spelled the way the git and image tags spell it."""
        out = stamp_chart(REAL_CHART, "0.13.0", "v0.13.0")
        assert '\nappVersion: "v0.13.0"\n' in out

    def test_the_placeholder_version_is_gone(self) -> None:
        """0.1.0/1.0 are the never-published defaults; shipping either would be the bug."""
        out = stamp_chart(REAL_CHART, "0.13.0", "v0.13.0")
        assert "version: 0.1.0" not in out
        assert 'appVersion: "1.0"' not in out

    def test_nothing_else_in_the_chart_is_touched(self) -> None:
        out = stamp_chart(REAL_CHART, "0.13.0", "v0.13.0")
        assert "name: flip-trust" in out
        assert "apiVersion: v2" in out
        assert len(out.splitlines()) == len(REAL_CHART.splitlines())

    def test_a_chart_without_the_keys_is_refused(self) -> None:
        with pytest.raises(StampError):
            stamp_chart("apiVersion: v2\nname: flip-trust\n", "0.13.0", "v0.13.0")

    def test_an_ambiguous_chart_is_refused_rather_than_guessed(self) -> None:
        """Two top-level `version:` lines mean the file changed shape; stamping a guess is worse."""
        with pytest.raises(StampError):
            stamp_chart('version: 0.1.0\nappVersion: "1.0"\nversion: 0.2.0\n', "0.13.0", "v0.13.0")

    def test_stamping_is_idempotent(self) -> None:
        """A re-run of a release re-stamps an already-stamped checkout."""
        once = stamp_chart(REAL_CHART, "0.13.0", "v0.13.0")
        assert stamp_chart(once, "0.13.0", "v0.13.0") == once


class TestValuesStamping:
    """The one edit that makes a versioned chart actually versioned."""

    def test_the_global_image_tag_is_pinned_to_the_release(self) -> None:
        out = stamp_values(REAL_VALUES, "v0.13.0")
        assert '    tag: "v0.13.0"' in out

    def test_the_empty_default_does_not_survive_publishing(self) -> None:
        """Empty means "each service's own mutable stag tag" -- the floating-images bug."""
        assert '    tag: ""' in REAL_VALUES, "fixture drifted: the default is no longer empty"
        out = stamp_values(REAL_VALUES, "v0.13.0")
        global_block = out[out.index("global:") : out.index("trustName:")]
        assert 'tag: ""' not in global_block

    def test_only_the_global_tag_moves(self) -> None:
        """Every per-service `tag: stag` is a deliberate default and must be left alone; the
        global pin beats them at render time anyway."""
        before = REAL_VALUES.count("tag: stag")
        assert before > 5, "fixture drifted: the per-service tags are gone"
        assert stamp_values(REAL_VALUES, "v0.13.0").count("tag: stag") == before

    def test_the_upstream_image_versions_are_untouched(self) -> None:
        """Grafana, Loki, Alloy and uv carry upstream versions that have nothing to do with ours."""
        out = stamp_values(REAL_VALUES, "v0.13.0")
        for pinned in ('tag: "3.4.0"', 'tag: "v1.9.0"', 'tag: "11.5.0"', 'tag: "python3.12-bookworm-slim"'):
            assert pinned in out

    def test_the_file_is_otherwise_byte_identical(self) -> None:
        """Comments included: values.yaml is documentation as much as configuration."""
        out = stamp_values(REAL_VALUES, "v0.13.0")
        changed = [(a, b) for a, b in zip(REAL_VALUES.splitlines(), out.splitlines(), strict=True) if a != b]
        assert len(changed) == 1, f"expected one changed line, got {changed}"

    def test_values_without_a_global_block_is_refused(self) -> None:
        with pytest.raises(StampError):
            stamp_values('trustName: x\n  image:\n    tag: ""\n', "v0.13.0")

    def test_a_global_block_without_an_image_tag_is_refused(self) -> None:
        with pytest.raises(StampError):
            stamp_values("global:\n  other: true\n\ntrustName: x\n", "v0.13.0")

    def test_stamping_is_idempotent(self) -> None:
        once = stamp_values(REAL_VALUES, "v0.13.0")
        assert stamp_values(once, "v0.13.0") == once


class TestPublishing:
    """The helm calls, with the filesystem and subprocess faked out."""

    @pytest.fixture(autouse=True)
    def _chart_on_disk(self) -> "object":
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = Path(tmp)
            self.chart = self.tmp / "flip-trust"
            self.chart.mkdir()
            (self.chart / "Chart.yaml").write_text(REAL_CHART)
            (self.chart / "values.yaml").write_text(REAL_VALUES)
            yield

    def _publish(self, **kwargs: object) -> tuple[Path, list[list[str]]]:
        calls: list[list[str]] = []
        with (
            mock.patch("package_chart.subprocess.run", side_effect=lambda a, **k: calls.append(a)),
            mock.patch("package_chart.shutil.which", return_value="/usr/bin/helm"),
        ):
            package = publish(raw_version="v0.13.0", registry=DEFAULT_REGISTRY, chart_dir=self.chart, **kwargs)  # type: ignore[arg-type]
        return package, calls

    def test_it_packages_then_pushes(self) -> None:
        package, calls = self._publish()
        assert len(calls) == 2
        assert calls[0][:2] == ["helm", "package"]
        assert calls[1][:2] == ["helm", "push"]
        assert calls[1][3] == DEFAULT_REGISTRY
        assert package.name == "flip-trust-0.13.0.tgz"

    def test_the_default_registry_is_the_projects_namespace(self) -> None:
        assert DEFAULT_REGISTRY == "oci://ghcr.io/londonaicentre/charts"

    def test_the_files_are_stamped_before_helm_runs(self) -> None:
        """helm packages what is on disk, so a stamp after packaging would publish the default."""
        seen: list[str] = []

        def record(argv: list[str], **_: object) -> None:
            seen.append((self.chart / "Chart.yaml").read_text())

        with (
            mock.patch("package_chart.subprocess.run", side_effect=record),
            mock.patch("package_chart.shutil.which", return_value="/usr/bin/helm"),
        ):
            publish(raw_version="v0.13.0", registry=DEFAULT_REGISTRY, chart_dir=self.chart)
        assert "version: 0.13.0" in seen[0]

    def test_no_push_stops_after_packaging(self) -> None:
        _, calls = self._publish(push=False)
        assert [c[:2] for c in calls] == [["helm", "package"]]

    def test_a_bad_version_stamps_nothing(self) -> None:
        """Validation comes first: a failed publish must not leave a half-stamped checkout."""
        with pytest.raises(StampError):
            publish(raw_version="stag", registry=DEFAULT_REGISTRY, chart_dir=self.chart, push=False)
        assert (self.chart / "Chart.yaml").read_text() == REAL_CHART

    def test_a_missing_chart_directory_is_refused(self) -> None:
        with pytest.raises(StampError):
            publish(raw_version="v0.13.0", registry=DEFAULT_REGISTRY, chart_dir=self.tmp / "nope", push=False)

    def test_the_published_values_pin_the_images(self) -> None:
        """The whole point of the slice: helm packages what is on disk, so if publish() forgets to
        write values.yaml the chart ships versioned but its images still float on `stag`."""
        self._publish()
        assert '    tag: "v0.13.0"' in (self.chart / "values.yaml").read_text()

    def test_the_published_values_leave_the_per_service_tags_alone(self) -> None:
        """Only the global pin moves on disk -- a greedier edit would rewrite Grafana and Loki too."""
        self._publish()
        published = (self.chart / "values.yaml").read_text()
        assert published.count("tag: stag") == REAL_VALUES.count("tag: stag")
        assert 'tag: "3.4.0"' in published

    def test_a_failed_helm_push_is_not_swallowed(self) -> None:
        with (
            mock.patch("package_chart.shutil.which", return_value="/usr/bin/helm"),
            mock.patch("package_chart.subprocess.run", side_effect=subprocess.CalledProcessError(1, "helm")),
            pytest.raises(subprocess.CalledProcessError),
        ):
            publish(raw_version="v0.13.0", registry=DEFAULT_REGISTRY, chart_dir=self.chart)

    def test_a_dry_run_stamps_but_calls_nothing(self) -> None:
        with mock.patch("package_chart.subprocess.run") as run:
            publish(raw_version="v0.13.0", registry=DEFAULT_REGISTRY, chart_dir=self.chart, dry_run=True)
        run.assert_not_called()
        assert "version: 0.13.0" in (self.chart / "Chart.yaml").read_text()
