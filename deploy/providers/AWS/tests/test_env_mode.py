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
"""The one HUB table (``deploy/env_mode.mk``, FLIP#1479).

Every Makefile that reads ``HUB`` — root, this provider, trust, trust/xnat, the Helm
chart, both fl-services, flip-ui — includes this file instead of carrying its own ``ifeq``
chain. This pins the table itself, the refusal of the retired ``PROD`` selector, and the
kit-file lookup that still finds a kit under its pre-FLIP#1479 name; the per-Makefile
probes (``test_trust_makefile_hub.py``, ``test_iam_permissions_boundary.py``) check the wiring.
"""

import subprocess
from pathlib import Path

import pytest

ENV_MODE_MK = Path(__file__).resolve().parents[4] / "deploy" / "env_mode.mk"
AWS_DIR = Path(__file__).resolve().parents[1]

_PROBE = f"""\
.DEFAULT_GOAL := __probe
include {ENV_MODE_MK}
__probe:
\t@printf '%s\\0' "$(ENV)" "$(ENV_FILE_NAME)" "$(__DCKR_SUFFIX)" "$(ENV_CLASS)" "$(IS_DEPLOYED)" \
"$(call kit_file,$(DIR),ABC)" "$(call kit_codes,$(DIR))"
"""


def _run(*variables: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-s", "-f", "-", "__probe", *variables],
        input=_PROBE,
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )


def _table(*variables: str) -> list[str]:
    result = _run(*variables)
    assert result.returncode == 0, result.stderr
    return result.stdout.split("\0")[:-1]


@pytest.mark.parametrize(
    ("hub", "env", "compose", "env_class"),
    [
        ("", "development", "development", "stag"),
        ("stag", "stag", "production", "stag"),
        ("prod", "prod", "production", "prod"),
    ],
)
def test_hub_table(hub: str, env: str, compose: str, env_class: str) -> None:
    assert _table(f"HUB={hub}")[:5] == [env, f".env.{env}", compose, env_class, hub]


def test_unknown_hub_value_is_refused() -> None:
    """A misspelt HUB must fail at parse time, never fall through to the development shape."""
    result = _run("HUB=production")
    assert result.returncode != 0
    assert "HUB must be unset, stag or prod" in result.stderr


@pytest.mark.parametrize(
    ("prod", "hint"),
    [
        ("true", "use HUB=prod"),
        ("lza", "use HUB=prod"),
        ("lza-stag", "use HUB=stag"),
        ("stag", "PROD=stag selected the retired self-contained staging estate and is no longer supported"),
        ("yes", "unset PROD and pass HUB"),
    ],
)
def test_any_prod_is_refused_naming_the_hub_equivalent(prod: str, hint: str) -> None:
    result = _run(f"PROD={prod}")
    assert result.returncode != 0
    assert "PROD is replaced by HUB=stag|prod" in result.stderr
    assert hint in result.stderr


def test_prod_in_the_environment_is_refused_too() -> None:
    result = subprocess.run(
        ["make", "-s", "-f", "-", "__probe"],
        input=_PROBE,
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "PROD": "true"},
    )
    assert result.returncode != 0
    assert "use HUB=prod" in result.stderr


@pytest.mark.parametrize(
    ("hub", "present", "found"),
    [
        ("prod", [".env.ABC.prod", ".env.ABC.production"], ".env.ABC.prod"),
        ("prod", [".env.ABC.production", ".env.ABC.lza-prod"], ".env.ABC.production"),
        ("prod", [".env.ABC.lza-prod"], ".env.ABC.lza-prod"),
        ("prod", [".env.ABC"], ".env.ABC"),
        ("prod", [".env.ABC.lza-stag"], ".env.ABC.prod"),
        ("stag", [".env.ABC.lza-stag"], ".env.ABC.lza-stag"),
        ("stag", [".env.ABC.production"], ".env.ABC.stag"),
        ("", [".env.ABC.development", ".env.ABC.production"], ".env.ABC.development"),
        ("prod", [], ".env.ABC.prod"),
    ],
)
def test_kit_file_finds_the_current_then_the_old_names(tmp_path: Path, hub: str, present: list[str], found: str):
    for name in present:
        (tmp_path / name).touch()
    assert _table(f"HUB={hub}", f"DIR={tmp_path}/")[5] == f"{tmp_path}/{found}"


def test_kit_codes_lists_every_kit_for_the_hub_old_names_included(tmp_path: Path) -> None:
    for name in [".env.A.prod", ".env.B.production", ".env.C.lza-prod", ".env.D.lza-stag", ".env.E.prod.example"]:
        (tmp_path / name).touch()
    assert _table("HUB=prod", f"DIR={tmp_path}/")[6] == "A B C"
    assert _table("HUB=stag", f"DIR={tmp_path}/")[6] == "D"


def test_old_kit_hint_names_the_rename() -> None:
    probe = f"""\
include {ENV_MODE_MK}
__probe:
\t@:$(call old_kit_hint,trust/.env.ABC.lza-prod)$(call old_kit_hint,trust/.env.XYZ)
\t@:$(call old_kit_hint,trust/.env.OK.prod)
"""
    result = subprocess.run(
        ["make", "-s", "-f", "-", "__probe", "HUB=prod"], input=probe, text=True, capture_output=True, check=True
    )
    assert result.stdout.splitlines() == [
        "⚠️  trust/.env.ABC.lza-prod is an old kit name — rename it to trust/.env.ABC.prod",
        "⚠️  trust/.env.XYZ is an old kit name — rename it to trust/.env.XYZ.prod",
    ]


def test_aws_makefile_refuses_an_unset_hub() -> None:
    """The AWS Makefile no longer defaults an unset selector to staging (FLIP#1377)."""
    result = subprocess.run(
        ["make", "-n", "plan"],
        text=True,
        capture_output=True,
        cwd=AWS_DIR,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode != 0
    assert "HUB=stag or HUB=prod is required" in result.stderr
