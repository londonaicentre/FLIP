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

"""One PROD -> kit-token map, in deploy/env_mode.mk, for the chart's preflight and sync-kit.

``preflight.sh`` used to carry its own ``if`` chain, written before the LZA tokens existed,
so ``PROD=lza-stag`` looked for ``trust/.env.<KIT>.development`` and failed check 4 while
the Makefile — which derives ENV from ``deploy/env_mode.mk`` — resolved the same kit
correctly. ``sync_k8s_kit.py`` carried a third copy of the same map.

Rather than keep three copies in step, the scripts now hold none: the Makefile injects the
token (``KIT_ENV=`` for preflight, ``--env $(ENV)`` for sync-kit), and a direct invocation
with ``PROD`` set but no token is refused with a message naming the Makefile. ``PROD`` unset
still means development, which is what a developer running the script bare expects.

The preflight variable is ``KIT_ENV`` rather than ``ENV`` because ``ENV`` is the POSIX
shell's own startup-file variable: an operator with ``ENV=~/.shrc`` exported would otherwise
have it read as a kit token by a direct ``bash preflight.sh``.

Every check the script performs is read-only, and KUBE_CONTEXT is pointed at a context that
does not exist so no cluster is contacted.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
PREFLIGHT = CHART_DIR / "scripts" / "preflight.sh"
SYNC_KIT = CHART_DIR / "sync_k8s_kit.py"

# PROD token -> the token in trust/.env.<CODE>.<token>, per deploy/env_mode.mk. The scripts
# no longer hold this map; it is pinned here only to assert the Makefile injects it.
ENV_TOKENS = [
    ("", "development"),
    ("stag", "stag"),
    ("true", "production"),
    ("lza", "lza-prod"),
    ("lza-stag", "lza-stag"),
]

NO_CLUSTER = {"KUBE_CONTEXT": "preflight-tests-no-such-context"}

#: Every Makefile target that runs sync_k8s_kit.py; each has its own recipe, so each must be
#: checked for the injected token separately.
SYNC_KIT_TARGETS = ["sync-kit-override", "patch-kit-secrets", "sync-kit"]


def _preflight(**env: str) -> subprocess.CompletedProcess:
    """Run the script with no reachable cluster."""
    return subprocess.run(
        ["bash", str(PREFLIGHT)],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home()), **NO_CLUSTER, **env},
    )


def _reported_env(stdout: str) -> str:
    match = re.search(r"KIT=\S+\s+\(env: (\S+)\)", stdout)
    assert match, f"preflight did not report the kit env:\n{stdout}"
    return match.group(1)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
@pytest.mark.parametrize(("prod", "token"), ENV_TOKENS)
def test_preflight_uses_the_injected_token_for_every_env(prod, token):
    """The Makefile's token is what reaches the script, LZA estates included."""
    assert _reported_env(_preflight(PROD=prod, KIT_ENV=token, KIT="ABC").stdout) == token


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_preflight_looks_for_the_lza_kit_file():
    """Check 4 must name the kit an LZA site actually has, not .development."""
    out = _preflight(PROD="lza-stag", KIT_ENV="lza-stag", KIT="ABC").stdout
    assert "trust/.env.ABC.lza-stag" in out, out
    assert "trust/.env.ABC.development" not in out, out


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_preflight_with_prod_but_no_env_is_refused_and_names_the_makefile():
    """No second copy of the map: a script that guesses is how the LZA tokens drifted."""
    result = _preflight(PROD="lza-stag", KIT="ABC")
    assert result.returncode == 1
    assert "PROD='lza-stag' is set but KIT_ENV is not" in result.stdout
    assert "make -C trust/deploy/helm preflight" in result.stdout
    # It must refuse rather than fall through to a kit file for the wrong environment.
    assert "trust/.env.ABC.development" not in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_preflight_without_prod_is_still_development():
    """A developer running the script bare gets the development kit, as before."""
    assert _reported_env(_preflight(KIT="ABC").stdout) == "development"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_preflight_ignores_the_posix_shell_env_variable():
    """`ENV` names the shell's startup file, so it must not be read as a kit token.

    An operator with `ENV=~/.shrc` exported — an ordinary thing to have — would otherwise
    see preflight look for `trust/.env.ABC./home/.../.shrc` and report the wrong kit.
    """
    out = _preflight(KIT="ABC", ENV="/home/someone/.shrc").stdout
    assert _reported_env(out) == "development"


def test_sync_kit_without_prod_resolves_the_development_kit():
    """The other half of the PROD-unset default, asserted on sync-kit rather than preflight.

    With no PROD and no --env the script must look for `trust/.env.<KIT>.development`. Only
    preflight had a test for that, so sync-kit could have drifted to any other default (or
    to a refusal) and stayed green.
    """
    result = subprocess.run(
        [sys.executable, str(SYNC_KIT), "--kit", "ABC"],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home())},
    )
    message = result.stdout + result.stderr
    # No such kit exists in a checkout, so it exits on the missing file — naming the one it
    # resolved, which is what this asserts.
    assert "trust/.env.ABC.development" in message, message
    assert "(env=development)" in message, message


def test_sync_kit_with_prod_but_no_env_is_refused_and_names_the_makefile():
    """sync_k8s_kit.py held a third copy of the map; it now requires --env when PROD is set."""
    result = subprocess.run(
        [sys.executable, str(SYNC_KIT), "--kit", "ABC"],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home()), "PROD": "lza-stag"},
    )
    assert result.returncode != 0
    message = result.stdout + result.stderr
    assert "PROD='lza-stag' is set but --env is not" in message, message
    assert "make -C trust/deploy/helm sync-kit" in message, message


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
    assert f'KIT_ENV="{token}"' in out, out


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize("target", SYNC_KIT_TARGETS)
@pytest.mark.parametrize(("prod", "token"), ENV_TOKENS)
def test_the_makefile_injects_the_env_mode_token_into_sync_kit(target, prod, token):
    """The other half of the single map: sync-kit is given `--env <token>`, never a guess.

    All three targets that invoke the script, not just `sync-kit-override`: they carry
    separate recipes, so a token dropped from `patch-kit-secrets` — the one that writes live
    credentials — would not have shown up here.
    """
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), target, "KIT=ABC", f"PROD={prod}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    assert f"--env {token}" in out, out
