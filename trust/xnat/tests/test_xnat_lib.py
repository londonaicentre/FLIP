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

"""xnat-lib.sh, the helpers configure-xnat.sh, configure-dcm2niix.sh and wait-for-xnat-plugins.sh share."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

CONFIG_DIR = Path(__file__).resolve().parents[1] / "xnat" / "config"
LIB = CONFIG_DIR / "xnat-lib.sh"
SCRIPTS = ("configure-xnat.sh", "configure-dcm2niix.sh", "wait-for-xnat-plugins.sh")
SOURCE_LINE = 'source "$(dirname "${BASH_SOURCE[0]}")/xnat-lib.sh"'
# A stand-in curl: prints CURL_BODY, then what -w '\n%{http_code}' would (a newline and CURL_STATUS),
# and exits CURL_EXIT, as curl does after a transport failure.
FAKE_CURL = '#!/bin/bash\nprintf "%s" "$CURL_BODY"\nprintf "\\n%s" "$CURL_STATUS"\nexit "$CURL_EXIT"\n'


def run(tmp_path: Path, snippet: str, body: str = "", status: str = "200", curl_exit: int = 0):
    """Runs ``snippet`` in bash with the library sourced and curl stood in for."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "curl").write_text(FAKE_CURL)
    (bin_dir / "curl").chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CURL_BODY": body,
        "CURL_STATUS": status,
        "CURL_EXIT": str(curl_exit),
    }
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; {snippet}'],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_xnat_curl_prints_the_body_of_a_2xx(tmp_path: Path) -> None:
    result = run(tmp_path, "xnat_curl http://xnat/data", body='{"ok":true}', status="201")

    assert result.returncode == 0, result.stderr
    assert result.stdout == '{"ok":true}\n'


def test_xnat_curl_fails_closed_and_never_logs_credentials_or_payloads(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        """xnat_curl -u 'admin:s3cret' -d '{"password":"p4ss"}' http://xnat/users""",  # pragma: allowlist secret
        body="refused",
        status="500",
    )

    assert result.returncode != 0
    assert "XNAT request failed with HTTP 500" in result.stderr
    assert "args: -u <redacted> -d <redacted> http://xnat/users" in result.stderr
    assert "body: refused" in result.stderr
    assert "s3cret" not in result.stderr
    assert "p4ss" not in result.stderr


def test_a_304_passes_only_when_the_caller_allows_it(tmp_path: Path) -> None:
    assert run(tmp_path, "xnat_curl http://xnat/x", status="304").returncode != 0
    assert run(tmp_path, "xnat_curl --allow-not-modified http://xnat/x", status="304").returncode == 0


def test_carriage_returns_cannot_hide_a_2xx(tmp_path: Path) -> None:
    result = run(tmp_path, "xnat_curl http://xnat/x", body="true\r", status="200\r")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "true\n"


def test_a_transport_failure_fails_xnat_curl_and_reads_as_000_from_xnat_request(tmp_path: Path) -> None:
    failed = run(tmp_path, "xnat_curl -u 'admin:s3cret' http://xnat/x", status="000", curl_exit=7)
    probed = run(tmp_path, 'xnat_request 5 10 http://xnat/x; echo "status=$xnat_status body=$xnat_body"', curl_exit=7)

    assert failed.returncode != 0
    assert "curl transport failure (exit 7)" in failed.stderr
    assert "s3cret" not in failed.stderr
    assert probed.returncode == 0, probed.stderr
    assert probed.stdout == "status=000 body=\n"


def test_xnat_request_hands_any_status_to_the_caller(tmp_path: Path) -> None:
    result = run(
        tmp_path, 'xnat_request 5 10 http://xnat/x; echo "status=$xnat_status body=$xnat_body"', body="no", status="401"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "status=401 body=no\n"


@pytest.mark.parametrize(("value", "accepted"), [("12", True), ("1/2", False), ("", False)])
def test_require_numeric_id(tmp_path: Path, value: str, accepted: bool) -> None:
    result = run(tmp_path, f"require_numeric_id 'XNAT listed a receiver' '{value}'; echo passed")

    assert (result.returncode == 0) is accepted
    if not accepted:
        assert f"ERROR: XNAT listed a receiver with id '{value}'" in result.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_each_script_loads_the_library_from_its_own_directory(script: str) -> None:
    """Relative to the script, not the caller's directory: the readiness test runs it from elsewhere."""
    text = (CONFIG_DIR / script).read_text()

    assert SOURCE_LINE in text, f"{script} does not source xnat-lib.sh from its own directory"
    for helper in ("scrub_args", "xnat_request", "xnat_curl", "wait_for_xnat", "require_numeric_id"):
        assert f"\n{helper}() {{" not in text, f"{script} defines its own {helper}"


def test_the_library_and_scripts_are_shellcheck_clean() -> None:
    shellcheck = shutil.which("shellcheck")
    if not shellcheck:
        pytest.skip("shellcheck not installed")
    files = [str(LIB), *(str(CONFIG_DIR / script) for script in SCRIPTS)]
    result = subprocess.run(
        [shellcheck, "-x", "-S", "warning", *files], cwd=CONFIG_DIR, capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0, result.stdout + result.stderr
