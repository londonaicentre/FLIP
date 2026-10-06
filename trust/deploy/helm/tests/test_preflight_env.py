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

"""preflight.sh resolves the kit-file env token for every PROD the platform defines.

The script used to carry its own ``if`` chain, written before the LZA tokens existed, so
``PROD=lza-stag`` looked for ``trust/.env.<KIT>.development`` and failed check 4 while the
Makefile — which derives ENV from ``deploy/env_mode.mk`` — resolved the same kit correctly.
The Makefile now injects ENV, and the script's own map is the fallback for a direct
invocation; both halves are pinned here.

Every check the script performs is read-only, and KUBE_CONTEXT is pointed at a context that
does not exist so no cluster is contacted.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
PREFLIGHT = CHART_DIR / "scripts" / "preflight.sh"

# PROD token -> the token in trust/.env.<CODE>.<token>, per deploy/env_mode.mk.
ENV_TOKENS = [
    ("", "development"),
    ("stag", "stag"),
    ("true", "production"),
    ("lza", "lza-prod"),
    ("lza-stag", "lza-stag"),
]

NO_CLUSTER = {"KUBE_CONTEXT": "preflight-tests-no-such-context"}


def _run_preflight(**env: str) -> str:
    """Run the script with no reachable cluster; return stdout (exit code is immaterial)."""
    return subprocess.run(
        ["bash", str(PREFLIGHT)],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home()), **NO_CLUSTER, **env},
    ).stdout


def _reported_env(stdout: str) -> str:
    match = re.search(r"KIT=\S+\s+\(env: (\S+)\)", stdout)
    assert match, f"preflight did not report the kit env:\n{stdout}"
    return match.group(1)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
@pytest.mark.parametrize(("prod", "token"), ENV_TOKENS)
def test_preflight_resolves_every_env_token(prod, token):
    """The fallback map covers the LZA estates; before this it fell through to development."""
    assert _reported_env(_run_preflight(PROD=prod, KIT="ABC")) == token


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_preflight_looks_for_the_lza_kit_file():
    """Check 4 must name the kit an LZA site actually has, not .development."""
    out = _run_preflight(PROD="lza-stag", KIT="ABC")
    assert "trust/.env.ABC.lza-stag" in out, out
    assert "trust/.env.ABC.development" not in out, out


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_injected_env_wins_over_the_fallback_map():
    """The Makefile derives ENV from deploy/env_mode.mk and injects it — one mapping, not two."""
    assert _reported_env(_run_preflight(PROD="lza", ENV="lza-prod", KIT="ABC")) == "lza-prod"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_an_unknown_prod_is_refused_not_treated_as_development():
    """`PROD=production` (a plausible typo) must not silently read a development kit."""
    result = subprocess.run(
        ["bash", str(PREFLIGHT)],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home()), **NO_CLUSTER, "PROD": "production"},
    )
    assert result.returncode == 1
    assert "is not a deployment mode" in result.stdout


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize(("prod", "token"), ENV_TOKENS)
def test_the_makefile_injects_the_env_mode_token_into_preflight(prod, token):
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "preflight", "KIT=ABC", f"PROD={prod}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    assert f'ENV="{token}"' in out, out
