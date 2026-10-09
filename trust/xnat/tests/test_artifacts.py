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

"""The third-party XNAT artifact caches: artifacts.manifest and scripts/xnat_artifacts.sh (FLIP#1292).

The script never reaches the network here. ``check`` must not download at all, which a ``curl``
stub that fails loudly proves; ``fetch`` reads either ``ARTIFACTS_DIR`` or a ``curl`` stub that
serves files from a fixture directory. Fixture manifests stand in for the real one, so the
checksums are of small generated files.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
XNAT_DIR = REPO_ROOT / "trust" / "xnat"
SCRIPT = XNAT_DIR / "scripts" / "xnat_artifacts.sh"
MANIFEST = XNAT_DIR / "artifacts.manifest"
XNAT_ENV = XNAT_DIR / ".env"
HELM_VALUES = REPO_ROOT / "trust" / "deploy" / "helm" / "values.yaml"
PREPARE_HINT = "make -C trust prepare-artifacts"

# The plugin families every FLIP XNAT runs. The manifest is the roster; this pins its membership,
# so a family dropped from the manifest fails here rather than as a readiness timeout on the route
# that plugin owns.
REQUIRED_FAMILIES = ("batch-launch", "container-service", "dicom-query-retrieve", "ohif-viewer")
FIXTURE_PLUGINS = ("batch-launch-1.0-xpl.jar", "ohif-viewer-2.0-fat.jar")


def _manifest_entries(path: Path = MANIFEST) -> list[tuple[str, str, str]]:
    entries = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        kind, sha, url = line.split()
        entries.append((kind, sha, url))
    return entries


def _make_upstream(tmp_path: Path, names: tuple[str, ...] = FIXTURE_PLUGINS) -> tuple[Path, Path]:
    """Write distinct fixture artifacts and a manifest pinning them.

    Returns:
        The directory holding the files under their upstream names, and the manifest path.
    """
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    lines = []
    for name in names:
        body = f"artifact {name}\n".encode()
        (upstream / name).write_bytes(body)
        lines.append(f"plugin {hashlib.sha256(body).hexdigest()} https://example.invalid/dl/{name}")
    manifest = tmp_path / "artifacts.manifest"
    manifest.write_text("# fixture\n" + "\n".join(lines) + "\n")
    return upstream, manifest


def _env(tmp_path: Path, manifest: Path, curl_body: str, **extra: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    curl = bin_dir / "curl"
    curl.write_text(f"#!/bin/sh\n{curl_body}\n")
    curl.chmod(0o755)
    aws = bin_dir / "aws"
    aws.write_text('#!/bin/sh\necho "aws must not be called" >&2\nexit 99\n')
    aws.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "XNAT_ARTIFACTS_MANIFEST": str(manifest),
        **extra,
    }


def _curl_serving(upstream: Path, log: Path | None = None) -> str:
    """A ``curl`` stub that answers ``-o <dest> <url>`` from ``upstream`` by file name."""
    record = f'echo "$url" >> "{log}"; ' if log else ""
    return (
        'out=""; url=""; while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; '
        "--retry|--retry-delay|--connect-timeout|--speed-limit|--speed-time) shift 2 ;; "
        '-*) shift ;; *) url="$1"; shift ;; esac; done; '
        f'{record}cp "{upstream}/${{url##*/}}" "$out"'
    )


NO_CURL = 'echo "curl must not be called" >&2; exit 98'


def _run(mode: str, kind: str, dest: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), mode, kind, str(dest)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


# --- the committed manifest -------------------------------------------------------------------


def test_manifest_lines_are_well_formed() -> None:
    entries = _manifest_entries()
    assert entries, "artifacts.manifest lists nothing"
    for kind, sha, url in entries:
        assert kind in ("war", "plugin"), kind
        assert re.fullmatch(r"[0-9a-f]{64}", sha), f"not a SHA-256: {sha}"
        assert url.startswith("https://"), f"not an https URL: {url}"


def test_manifest_war_is_the_pinned_xnat_version() -> None:
    """The image build copies ``xnat-web-${XNAT_VERSION}.war``; a WAR line for another version fails it."""
    version = re.search(r"^XNAT_VERSION=(\S+)$", XNAT_ENV.read_text(), re.MULTILINE)
    assert version is not None, f"no XNAT_VERSION in {XNAT_ENV}"
    wars = [url.rsplit("/", 1)[-1] for kind, _, url in _manifest_entries() if kind == "war"]
    assert wars == [f"xnat-web-{version.group(1)}.war"]


def test_manifest_lists_one_jar_per_required_plugin_family() -> None:
    jars = [url.rsplit("/", 1)[-1] for kind, _, url in _manifest_entries() if kind == "plugin"]
    for family in REQUIRED_FAMILIES:
        matching = [jar for jar in jars if jar.startswith(f"{family}-")]
        assert len(matching) == 1, f"{family}: expected one jar, found {matching}"
    assert len(jars) == len(REQUIRED_FAMILIES), f"unexpected plugins in the manifest: {jars}"


def _chart_plugin_urls() -> dict[str, str]:
    """Read the Helm chart's ``xnat.web.plugins.urls`` map.

    Parsed by indentation rather than with a YAML loader so this suite keeps its single ``pydicom``
    dependency.
    """
    lines = HELM_VALUES.read_text().splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(r"^\s*urls:\s*$", line)]
    assert len(starts) == 1, f"expected one urls: block in {HELM_VALUES}, found {len(starts)}"
    indent = len(lines[starts[0]]) - len(lines[starts[0]].lstrip())
    urls: dict[str, str] = {}
    for line in lines[starts[0] + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        entry = re.match(r'^(\s+)([A-Za-z0-9_-]+):\s*"?([^"\s]+)"?\s*$', line)
        if entry is None or len(entry.group(1)) <= indent:
            break
        urls[entry.group(2)] = entry.group(3)
    return urls


def test_the_helm_chart_downloads_exactly_the_manifest_plugins() -> None:
    """The K8s init container downloads its own roster into an emptyDir that masks the image's plugins.

    A URL in one place and not the other leaves Kubernetes trusts on a different plugin build from
    Compose ones, which nothing reports until XNAT is serving. Each chart key must also be the jar's
    family, which ``make status`` relies on to pair keys with jars.
    """
    chart = _chart_plugin_urls()
    manifest = sorted(url for kind, _, url in _manifest_entries() if kind == "plugin")
    assert sorted(chart.values()) == manifest
    for key, url in chart.items():
        assert url.rsplit("/", 1)[-1].startswith(f"{key}-"), f"chart key {key} does not name {url}"


# --- check ------------------------------------------------------------------------------------


def test_check_passes_a_complete_cache_without_downloading(tmp_path: Path) -> None:
    upstream, manifest = _make_upstream(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    for name in FIXTURE_PLUGINS:
        (cache / name).write_bytes((upstream / name).read_bytes())

    result = _run("check", "plugin", cache, _env(tmp_path, manifest, NO_CURL))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "checksums verified" in result.stdout


def test_check_fails_on_a_cold_cache_naming_the_prep_step(tmp_path: Path) -> None:
    _, manifest = _make_upstream(tmp_path)

    result = _run("check", "plugin", tmp_path / "cache", _env(tmp_path, manifest, NO_CURL))

    assert result.returncode == 3
    assert PREPARE_HINT in result.stdout
    assert "missing batch-launch-1.0-xpl.jar" in result.stdout
    assert "curl must not be called" not in result.stderr


@pytest.mark.parametrize(
    "corruption",
    [
        pytest.param(b"", id="zero-byte"),
        pytest.param(b"PK\x03\x04 a truncated or different build", id="wrong-bytes"),
    ],
)
def test_check_rejects_a_jar_that_is_not_the_pinned_build(tmp_path: Path, corruption: bytes) -> None:
    """Dev bind-mounts the cache into the running XNAT, so a wrong jar would boot without its routes."""
    upstream, manifest = _make_upstream(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    for name in FIXTURE_PLUGINS:
        (cache / name).write_bytes((upstream / name).read_bytes())
    (cache / FIXTURE_PLUGINS[1]).write_bytes(corruption)

    result = _run("check", "plugin", cache, _env(tmp_path, manifest, NO_CURL))

    assert result.returncode == 3
    assert f"checksum mismatch {FIXTURE_PLUGINS[1]}" in result.stdout


def test_check_rejects_a_jar_the_manifest_does_not_list(tmp_path: Path) -> None:
    """XNAT loads every jar in the directory, so a stale version beside the new one loads both."""
    upstream, manifest = _make_upstream(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    for name in FIXTURE_PLUGINS:
        (cache / name).write_bytes((upstream / name).read_bytes())
    (cache / "batch-launch-0.9-xpl.jar").write_bytes(b"old")

    result = _run("check", "plugin", cache, _env(tmp_path, manifest, NO_CURL))

    assert result.returncode == 3
    assert "not in the manifest batch-launch-0.9-xpl.jar" in result.stdout


# --- fetch ------------------------------------------------------------------------------------


def test_fetch_downloads_a_cold_cache_from_the_manifest_urls(tmp_path: Path) -> None:
    upstream, manifest = _make_upstream(tmp_path)
    log = tmp_path / "curl.log"
    cache = tmp_path / "cache"

    result = _run("fetch", "plugin", cache, _env(tmp_path, manifest, _curl_serving(upstream, log)))

    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(p.name for p in cache.iterdir()) == sorted(FIXTURE_PLUGINS)
    assert log.read_text().split() == [f"https://example.invalid/dl/{name}" for name in FIXTURE_PLUGINS]


def test_fetch_bounds_a_stalled_download(tmp_path: Path) -> None:
    """A host that accepts the connection and goes quiet must fail the download, not hang the build."""
    upstream, manifest = _make_upstream(tmp_path)
    args = tmp_path / "curl.args"
    cache = tmp_path / "cache"

    curl = f'echo "$*" >> "{args}"; ' + _curl_serving(upstream)
    result = _run("fetch", "plugin", cache, _env(tmp_path, manifest, curl))

    assert result.returncode == 0, result.stdout + result.stderr
    calls = args.read_text().splitlines()
    assert len(calls) == len(FIXTURE_PLUGINS), calls
    for call in calls:
        assert re.search(r"--connect-timeout [1-9]", call), call
        assert re.search(r"--speed-limit [1-9]\d* --speed-time [1-9]", call), call


def test_fetch_keeps_matching_files_and_downloads_nothing(tmp_path: Path) -> None:
    upstream, manifest = _make_upstream(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    for name in FIXTURE_PLUGINS:
        (cache / name).write_bytes((upstream / name).read_bytes())

    result = _run("fetch", "plugin", cache, _env(tmp_path, manifest, NO_CURL))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "already present" in result.stdout


def test_fetch_copies_from_artifacts_dir_without_the_network(tmp_path: Path) -> None:
    """A host with no route to upstream hands the files over in a directory instead."""
    upstream, manifest = _make_upstream(tmp_path)
    cache = tmp_path / "cache"

    result = _run("fetch", "plugin", cache, _env(tmp_path, manifest, NO_CURL, ARTIFACTS_DIR=str(upstream)))

    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(p.name for p in cache.iterdir()) == sorted(FIXTURE_PLUGINS)


def test_fetch_names_the_upstream_url_of_a_file_missing_from_artifacts_dir(tmp_path: Path) -> None:
    upstream, manifest = _make_upstream(tmp_path)
    (upstream / FIXTURE_PLUGINS[0]).unlink()

    result = _run("fetch", "plugin", tmp_path / "cache", _env(tmp_path, manifest, NO_CURL, ARTIFACTS_DIR=str(upstream)))

    assert result.returncode != 0
    assert f"https://example.invalid/dl/{FIXTURE_PLUGINS[0]}" in result.stderr


def test_fetch_refuses_a_download_with_the_wrong_checksum_and_installs_nothing(tmp_path: Path) -> None:
    """A changed or truncated upstream file must fail the fetch, never land in the cache or an image."""
    upstream, manifest = _make_upstream(tmp_path)
    (upstream / FIXTURE_PLUGINS[0]).write_bytes(b"tampered")
    cache = tmp_path / "cache"

    result = _run("fetch", "plugin", cache, _env(tmp_path, manifest, _curl_serving(upstream)))

    assert result.returncode != 0
    assert "but the manifest pins" in result.stderr
    assert list(cache.iterdir()) == [], "a rejected or partial download was left in the cache"


def test_fetch_propagates_a_failed_download(tmp_path: Path) -> None:
    _, manifest = _make_upstream(tmp_path)
    cache = tmp_path / "cache"

    result = _run("fetch", "plugin", cache, _env(tmp_path, manifest, "exit 22"))

    assert result.returncode != 0
    assert "Download failed" in result.stderr
    assert list(cache.iterdir()) == []


def test_fetch_replaces_a_corrupt_jar_and_removes_unlisted_ones(tmp_path: Path) -> None:
    """The S3 cache this replaces left stale versions and a stamp file behind; fetch converges on the manifest."""
    upstream, manifest = _make_upstream(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / FIXTURE_PLUGINS[0]).write_bytes(b"")
    (cache / "batch-launch-0.9-xpl.jar").write_bytes(b"old")
    (cache / ".s3-prefix").write_text("xnat-1.10.0/plugins\n")

    result = _run("fetch", "plugin", cache, _env(tmp_path, manifest, _curl_serving(upstream)))

    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(p.name for p in cache.iterdir()) == sorted(FIXTURE_PLUGINS)
    assert (cache / FIXTURE_PLUGINS[0]).read_bytes() == (upstream / FIXTURE_PLUGINS[0]).read_bytes()


def test_fetch_war_leaves_other_wars_in_the_build_context(tmp_path: Path) -> None:
    """Only the named WAR is copied into the image, so another version's WAR is not a stale plugin."""
    body = b"war bytes"
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / "xnat-web-9.9.war").write_bytes(body)
    manifest = tmp_path / "artifacts.manifest"
    manifest.write_text(f"war {hashlib.sha256(body).hexdigest()} https://example.invalid/xnat-web-9.9.war\n")
    context = tmp_path / "build-artifacts"
    context.mkdir()
    (context / "xnat-web-9.8.war").write_bytes(b"older")

    result = _run("fetch", "war", context, _env(tmp_path, manifest, _curl_serving(upstream)))

    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(p.name for p in context.iterdir()) == ["xnat-web-9.8.war", "xnat-web-9.9.war"]


def test_neither_mode_ever_calls_aws(tmp_path: Path) -> None:
    upstream, manifest = _make_upstream(tmp_path)
    cache = tmp_path / "cache"
    env = _env(tmp_path, manifest, _curl_serving(upstream))

    for mode in ("fetch", "check"):
        result = _run(mode, "plugin", cache, env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "aws must not be called" not in result.stderr


# --- make wiring ------------------------------------------------------------------------------


def test_prepare_artifacts_fills_the_dev_plugin_cache() -> None:
    result = subprocess.run(
        ["make", "-n", "-C", "trust", "prepare-artifacts", "FL_BACKEND=nvflare"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "xnat-plugins-download" in result.stdout


def test_the_dev_plugin_targets_need_no_bucket_or_aws() -> None:
    makefile = (XNAT_DIR / "Makefile").read_text()
    assert "FLIP_ARTIFACTS_BUCKET_NAME" not in makefile
    assert "aws s3" not in makefile
