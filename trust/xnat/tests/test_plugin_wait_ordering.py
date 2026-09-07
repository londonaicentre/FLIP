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
"""
The plugin-readiness wait must run *after* site initialization (FLIP#966).

``wait-for-xnat-plugins.sh`` probes an authenticated plugin route. An uninitialized
XNAT redirects **every** authenticated route to ``/setup`` — ``/data/projects`` does
it too, so this is not a plugin-specific behaviour — which means the probe cannot
answer until ``configure-xnat.sh`` has POSTed ``{"initialized": true}``.

Running the wait before that POST deadlocks: the loop blocks on a condition that
only a later step in the same script can satisfy. Every fresh trust bring-up failed
this way, after burning the full timeout and then blaming the DQR plugin — which was
loaded and fine.

Two things are pinned here:

1. the ordering in ``configure-xnat.sh``, so the wait cannot drift back above the
   init POST or below the first plugin-dependent call; and
2. the probe's own behaviour on a redirect — it must fail fast and say why, rather
   than treat it as a transient and poll until timeout.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIGURE_XNAT = REPO_ROOT / "trust/xnat/xnat/config/configure-xnat.sh"
PLUGIN_WAIT = REPO_ROOT / "trust/xnat/xnat/config/wait-for-xnat-plugins.sh"

# A wedged wait is a hung bring-up; a test that inherits that hang is useless.
TIMEOUT_SECONDS = 30


def _line_of(pattern: str, path: Path = CONFIGURE_XNAT) -> int:
    """Find the 1-indexed line number matching a regex, asserting it is unique enough.

    Args:
        pattern: Regex to search for.
        path: Script to search.

    Returns:
        The 1-indexed line number of the first match.
    """
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if re.search(pattern, line) and not line.lstrip().startswith("#"):
            return number
    pytest.fail(f"no non-comment line matching {pattern!r} in {path.name}")


def test_plugin_wait_runs_after_site_initialization() -> None:
    """The ordering that FLIP#966 was: init must come first, or the wait deadlocks."""
    init_line = _line_of(r'\\"initialized\\": true')
    wait_line = _line_of(r"bash wait-for-xnat-plugins\.sh")

    assert init_line < wait_line, (
        f"wait-for-xnat-plugins.sh runs at line {wait_line}, before site initialization at "
        f"line {init_line}. An uninitialized XNAT redirects every authenticated route to "
        "/setup, so the probe can never answer and the bring-up deadlocks (FLIP#966)."
    )


def test_plugin_wait_runs_before_any_plugin_dependent_call() -> None:
    """It still has to guard what it exists to guard — the DQR and OHIF config."""
    wait_line = _line_of(r"bash wait-for-xnat-plugins\.sh")
    dqr_line = _line_of(r"Configuring DQR plugin")

    assert wait_line < dqr_line, (
        f"wait-for-xnat-plugins.sh runs at line {wait_line}, after the DQR configuration at "
        f"line {dqr_line}. Plugin settings would then race a Tomcat that has not registered "
        "its plugin routes, leaving XNAT half-configured."
    )


@pytest.mark.parametrize("redirect_status", ["301", "302", "307"])
def test_probe_fails_fast_on_a_redirect(tmp_path: Path, redirect_status: str) -> None:
    """A redirect means "site not initialized" — never "plugin still loading".

    Waiting cannot clear it, so the script must exit non-zero straight away rather
    than poll to timeout. Driven with a stub ``curl`` on PATH, the same mock-bin
    idiom as ``test_guard_scripts.py``.
    """
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    # -w '%{http_code}' asks for the status; -w '%{redirect_url}' asks for the target. The stub
    # answers whichever this invocation requested, so the script's own error path works too.
    (stub_bin / "curl").write_text(
        "#!/bin/sh\n"
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        "    '%{redirect_url}') printf 'http://xnat-web:8080/setup'; exit 0 ;;\n"
        f"    '%{{http_code}}') printf '{redirect_status}'; exit 0 ;;\n"
        "  esac\n"
        "done\n"
        f"printf '{redirect_status}'\n"
    )
    (stub_bin / "curl").chmod(0o755)

    env = {
        "PATH": f"{stub_bin}:/usr/bin:/bin",
        "XNAT_ADMIN_USER": "admin",
        "XNAT_ADMIN_INITIAL_PASSWORD": "initial",  # pragma: allowlist secret
        "XNAT_ADMIN_PASSWORD": "rotated",  # pragma: allowlist secret
        # Generous budget on purpose: if the script polls instead of failing fast, the assertion
        # below must fail on the exit path rather than be rescued by a short timeout.
        "XNAT_PLUGIN_READINESS_TIMEOUT_SECONDS": "900",
        "XNAT_PLUGIN_READINESS_POLL_SECONDS": "1",
    }

    result = subprocess.run(
        ["bash", str(PLUGIN_WAIT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )

    assert result.returncode != 0, "a redirect must fail the wait, not satisfy it"
    combined = result.stdout + result.stderr
    assert "not initialized" in combined, (
        "the error must name site initialization as the cause — a bare 'DQR not ready' sends "
        f"the reader after a plugin that is fine. Got:\n{combined}"
    )
    assert "DQR not ready yet" not in combined, (
        "the script polled on a redirect instead of failing fast; it would burn the whole "
        f"timeout budget. Got:\n{combined}"
    )


def test_probe_still_succeeds_on_2xx(tmp_path: Path) -> None:
    """The happy path is untouched — a ready plugin route still ends the wait."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    (stub_bin / "curl").write_text("#!/bin/sh\nprintf '200'\n")
    (stub_bin / "curl").chmod(0o755)

    result = subprocess.run(
        ["bash", str(PLUGIN_WAIT)],
        env={
            "PATH": f"{stub_bin}:/usr/bin:/bin",
            "XNAT_ADMIN_USER": "admin",
            "XNAT_ADMIN_INITIAL_PASSWORD": "initial",  # pragma: allowlist secret
            "XNAT_ADMIN_PASSWORD": "rotated",  # pragma: allowlist secret
            "XNAT_PLUGIN_READINESS_TIMEOUT_SECONDS": "30",
            "XNAT_PLUGIN_READINESS_POLL_SECONDS": "1",
        },
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )

    assert result.returncode == 0, f"a 200 must satisfy the wait. Got:\n{result.stdout}{result.stderr}"
    assert "is ready" in result.stdout


def test_shellcheck_clean_if_available() -> None:
    """Cheap belt-and-braces on the edited script when shellcheck is installed."""
    shellcheck = shutil.which("shellcheck")
    if not shellcheck:
        pytest.skip("shellcheck not installed")

    result = subprocess.run(
        [shellcheck, "-S", "error", str(PLUGIN_WAIT), str(CONFIGURE_XNAT)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        cwd=os.fspath(REPO_ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr
