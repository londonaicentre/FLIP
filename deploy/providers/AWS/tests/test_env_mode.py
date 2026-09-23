#  Copyright 2026 London AI Centre
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""The one PROD table (``deploy/env_mode.mk``, FLIP#749).

Every Makefile that reads ``PROD`` — root, this provider, trust, trust/xnat, the Helm
chart, both fl-services — includes this file instead of carrying its own ``ifeq`` chain, so a
new PROD value is added in exactly one place. This pins the table itself; the
per-Makefile probes (``test_trust_makefile_lza_modes.py``, ``test_lza_iam_boundary.py``)
check the wiring.
"""
import subprocess
from pathlib import Path

import pytest

ENV_MODE_MK = Path(__file__).resolve().parents[4] / "deploy" / "env_mode.mk"

_PROBE = f"""\
.DEFAULT_GOAL := __probe
include {ENV_MODE_MK}
__probe:
\t@printf '%s\\0' "$(ENV)" "$(ENV_FILE_NAME)" "$(__DCKR_SUFFIX)" "$(ENV_CLASS)" "$(IS_LZA)" "$(IS_DEPLOYED)"
"""


def _table(*variables: str) -> list[str]:
    result = subprocess.run(
        ["make", "-s", "-f", "-", "__probe", *variables], input=_PROBE, text=True, capture_output=True, check=True
    )
    return result.stdout.split("\0")[:-1]


@pytest.mark.parametrize(
    ("prod", "env", "compose", "env_class", "is_lza", "is_deployed"),
    [
        ("", "development", "development", "stag", "", ""),
        ("stag", "stag", "production", "stag", "", "stag"),
        ("true", "production", "production", "prod", "", "true"),
        ("lza", "lza-prod", "production", "prod", "lza", "lza"),
        ("lza-stag", "lza-stag", "production", "stag", "lza-stag", "lza-stag"),
    ],
)
def test_prod_table(prod: str, env: str, compose: str, env_class: str, is_lza: str, is_deployed: str) -> None:
    assert _table(f"PROD={prod}") == [env, f".env.{env}", compose, env_class, is_lza, is_deployed]


def test_env_mode_prod_overrides_prod() -> None:
    """The AWS Makefile maps an unset PROD to staging by handing the include ENV_MODE_PROD=stag."""
    assert _table("PROD=", "ENV_MODE_PROD=stag")[0] == "stag"


def test_unknown_prod_value_is_refused() -> None:
    """A misspelt PROD must fail at parse time, never fall through to the development shape."""
    result = subprocess.run(
        ["make", "-s", "-f", "-", "__probe", "PROD=prod"], input=_PROBE, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "PROD must be unset, stag, true, lza or lza-stag" in result.stderr
