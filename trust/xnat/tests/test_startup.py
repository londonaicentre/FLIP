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

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENSURE_PLUGINS = REPO_ROOT / "trust" / "xnat" / "scripts" / "ensure_plugins.sh"
WAIT_FOR_PLUGINS = REPO_ROOT / "trust" / "xnat" / "xnat" / "config" / "wait-for-xnat-plugins.sh"
PLUGIN_PREFIX = "xnat-1.10.0/plugins"
REQUIRED_PLUGIN_NAMES = (
    "batch-launch-test.jar",
    "container-service-test.jar",
    "dicom-query-retrieve-test.jar",
)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _plugin_env(tmp_path: Path, aws_body: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "aws", f"#!/bin/sh\nset -eu\n{aws_body}\n")
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _run_plugin_check(plugin_dir: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ENSURE_PLUGINS), str(plugin_dir), "test-artifacts", PLUGIN_PREFIX],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def test_plugin_check_skips_aws_for_matching_complete_cache(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    for name in REQUIRED_PLUGIN_NAMES:
        (plugin_dir / name).touch()
    (plugin_dir / ".s3-prefix").write_text(f"{PLUGIN_PREFIX}\n")
    env = _plugin_env(tmp_path, 'echo "AWS must not be called" >&2; exit 99')

    result = _run_plugin_check(plugin_dir, env)

    assert result.returncode == 0, result.stderr
    assert "Skipping S3 sync" in result.stdout


def test_plugin_check_downloads_and_validates_fresh_cache(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    create_plugins = 'dest="$4"; mkdir -p "$dest"; touch ' + " ".join(
        f'"$dest/{name}"' for name in REQUIRED_PLUGIN_NAMES
    )
    env = _plugin_env(tmp_path, create_plugins)

    result = _run_plugin_check(plugin_dir, env)

    assert result.returncode == 0, result.stderr
    assert (plugin_dir / ".s3-prefix").read_text().strip() == PLUGIN_PREFIX


def test_plugin_check_propagates_sync_failure(tmp_path: Path) -> None:
    result = _run_plugin_check(tmp_path / "plugins", _plugin_env(tmp_path, "exit 42"))

    assert result.returncode == 42


def test_plugin_check_rejects_incomplete_download(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    env = _plugin_env(
        tmp_path,
        'dest="$4"; mkdir -p "$dest"; touch "$dest/batch-launch-test.jar" "$dest/container-service-test.jar"',
    )

    result = _run_plugin_check(plugin_dir, env)

    assert result.returncode != 0
    assert "dicom-query-retrieve-" in result.stdout


def _readiness_env(tmp_path: Path, curl_body: str, timeout: str = "5") -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "curl", f"#!/bin/sh\nset -eu\n{curl_body}\n")
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "XNAT_ADMIN_USER": "admin",
        "XNAT_ADMIN_INITIAL_PASSWORD": "test-password",
        "XNAT_ADMIN_PASSWORD": "test-password",
        "XNAT_PLUGIN_READINESS_TIMEOUT_SECONDS": timeout,
        "XNAT_PLUGIN_READINESS_POLL_SECONDS": "0",
    }


def test_readiness_waits_for_authenticated_plugin_route(tmp_path: Path) -> None:
    count_file = tmp_path / "curl-count"
    curl_body = (
        f'count=$(cat "{count_file}" 2>/dev/null || echo 0); count=$((count + 1)); '
        f'printf "%s" "$count" > "{count_file}"; '
        'if [ "$count" -ge 2 ]; then printf "204"; else printf "404"; fi'
    )

    result = subprocess.run(
        ["bash", str(WAIT_FOR_PLUGINS)],
        check=False,
        capture_output=True,
        env=_readiness_env(tmp_path, curl_body),
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "ready (HTTP 204)" in result.stdout


def test_readiness_fails_with_endpoint_and_status_on_timeout(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(WAIT_FOR_PLUGINS)],
        check=False,
        capture_output=True,
        env=_readiness_env(tmp_path, 'printf "404"', timeout="0"),
        text=True,
    )

    assert result.returncode != 0
    assert "/xapi/dqr/settings" in result.stderr
    assert "last HTTP status: 404" in result.stderr
    assert "test-password" not in result.stdout + result.stderr


def test_root_smoke_target_resolves_relative_paths_from_repo_root() -> None:
    result = subprocess.run(
        [
            "make",
            "-n",
            "e2e_smoke",
            "MODEL_FILES_DIR=fl-tutorials/example/app_files",
            "QUERY_FILE=fl-tutorials/example/query.sql",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert f'MODEL_FILES_DIR="{REPO_ROOT}/fl-tutorials/example/app_files"' in result.stdout
    assert f'QUERY_FILE="{REPO_ROOT}/fl-tutorials/example/query.sql"' in result.stdout


def test_aggregate_startup_targets_propagate_child_failures() -> None:
    trust_makefile = (REPO_ROOT / "trust" / "Makefile").read_text()
    xnat_makefile = (REPO_ROOT / "trust" / "xnat" / "Makefile").read_text()

    assert '$(MAKE) up-trust KIT=$$kit || { echo "❌ Failed to start trust $$kit"; exit 1; }' in trust_makefile
    assert '$(MAKE) up-xnat KIT=$$kit || { echo "❌ Failed to start XNAT for trust $$kit"; exit 1; }' in xnat_makefile
