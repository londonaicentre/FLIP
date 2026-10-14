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

"""The chart's preflight and sync-kit find the kit the Makefile resolved (FLIP#1479).

The kit-file token is HUB itself (``deploy/env_mode.mk``): ``stag`` / ``prod``, or
``development`` when HUB is unset, so the scripts hold no map of their own. The Makefile also
hands them the kit file it resolved (``KIT_FILE=`` for preflight, ``--kit-file`` for sync-kit),
which may still carry its pre-FLIP#1479 name (``.production``, ``.lza-prod``, ``.lza-stag``).

The preflight never reads ``ENV``: that is the POSIX shell's own startup-file variable, and an
operator with ``ENV=~/.shrc`` exported would otherwise have it read as a kit token.

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

# HUB -> the token in trust/.env.<CODE>.<token>, per deploy/env_mode.mk.
ENV_TOKENS = [("", "development"), ("stag", "stag"), ("prod", "prod")]

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
@pytest.mark.parametrize(("hub", "token"), ENV_TOKENS)
def test_preflight_takes_the_token_from_hub(hub, token):
    assert _reported_env(_preflight(HUB=hub, KIT="ABC").stdout) == token


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_preflight_reads_the_kit_file_the_makefile_resolved(tmp_path):
    """An old-named kit the Makefile found is the one check 4 reads."""
    kit = tmp_path / ".env.ABC.lza-prod"
    kit.write_text("TRUST_API_KEY=k\nAES_KEY_BASE64=a\nCENTRAL_HUB_API_URL=https://hub\n")
    out = _preflight(HUB="prod", KIT="ABC", KIT_FILE=str(kit)).stdout
    assert f"Kit file: {kit}" in out, out
    assert "all set" in out, out


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_preflight_ignores_the_posix_shell_env_variable():
    """`ENV` names the shell's startup file, so it must not be read as a kit token."""
    out = _preflight(KIT="ABC", ENV="/home/someone/.shrc").stdout
    assert _reported_env(out) == "development"


def _sync_kit(*args: str, **env: str) -> str:
    result = subprocess.run(
        [sys.executable, str(SYNC_KIT), "--kit", "ABC", *args],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home()), **env},
    )
    # No such kit exists in a checkout, so it exits on the missing file — naming the one it
    # resolved, which is what the tests assert.
    return result.stdout + result.stderr


@pytest.mark.parametrize(("hub", "token"), ENV_TOKENS)
def test_sync_kit_takes_the_token_from_hub(hub, token):
    message = _sync_kit(HUB=hub)
    assert f"trust/.env.ABC.{token}" in message, message
    assert f"(env={token})" in message, message


def test_sync_kit_reads_the_kit_file_it_is_given(tmp_path):
    kit = tmp_path / ".env.ABC.production"
    message = _sync_kit("--env", "prod", "--kit-file", str(kit))
    assert f"Kit file not found: {kit}" in message, message


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize(("hub", "token"), ENV_TOKENS)
def test_the_makefile_injects_hub_and_the_kit_file_into_preflight(hub, token):
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "preflight", "KIT=ABC", f"HUB={hub}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    assert f'HUB="{hub}"' in out, out
    assert f'KIT_FILE="{CHART_DIR.parents[2]}/trust/.env.ABC.{token}"' in out, out


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize("target", SYNC_KIT_TARGETS)
@pytest.mark.parametrize(("hub", "token"), ENV_TOKENS)
def test_the_makefile_injects_the_token_and_kit_file_into_sync_kit(target, hub, token):
    """All three targets that invoke the script carry separate recipes, so each is checked —
    a token dropped from `patch-kit-secrets`, the one that writes live credentials, would
    not show up otherwise.
    """
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), target, "KIT=ABC", f"HUB={hub}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    assert f"--env {token}" in out, out
    assert f"--kit-file {CHART_DIR.parents[2]}/trust/.env.ABC.{token}" in out, out
