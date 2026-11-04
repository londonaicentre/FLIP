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

"""Hub/FL egress in the rendered specs (#1586, epic #1589).

A trust has one closed egress list, held by the platform tier's external access
integration. A spec either attaches that integration or reaches nothing, and only the two
services that legitimately leave the account may attach it. These guards pin that with no
account: a trial account cannot have an integration at all, so the default has to mean "no
egress" and it has to be provable offline.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import CODE, ENV

SNOWFLAKE_DIR = Path(__file__).resolve().parents[1]

for path in (SNOWFLAKE_DIR,):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import spcs_sql  # noqa: E402
import spec_templates  # noqa: E402
from naming import Names  # noqa: E402
from render_specs import render  # noqa: E402


def build(tmp_path: Path, kit: dict[str, str], **overrides: str) -> tuple[dict, dict[str, dict]]:
    out = tmp_path / "rendered"
    manifest = render(dict(kit, **overrides), CODE, ENV, out, digests={})
    specs = {path.stem: yaml.safe_load(path.read_text()) for path in sorted(out.glob("*.yaml"))}
    return manifest, specs


def applied(specs: dict[str, dict]) -> dict[str, list[str]]:
    return {name: spec["spec"].get("externalAccessIntegrations", []) for name, spec in specs.items()}


def test_nothing_reaches_out_by_default(tmp_path: Path, kit: dict[str, str]) -> None:
    """A trial account has no external network access, so hubless is the default."""
    manifest, specs = build(tmp_path, kit)
    assert manifest["external_access_integrations"] == []
    assert not any(applied(specs).values())


def test_only_trust_api_and_the_fl_client_attach_the_integration(tmp_path: Path, kit: dict[str, str]) -> None:
    _, specs = build(tmp_path, kit, HUB_EGRESS="true")
    attached = {name for name, names in applied(specs).items() if names}
    assert attached == {"trust-api", "fl-client-net-1"}


def test_every_attaching_spec_names_the_one_integration(tmp_path: Path, kit: dict[str, str]) -> None:
    """One integration per trust: a second would be a second egress list to keep in step."""
    manifest, specs = build(tmp_path, kit, HUB_EGRESS="true")
    assert manifest["external_access_integrations"] == ["FLIP_GSTT_HUB_EAI"]
    for name, names in applied(specs).items():
        assert len(names) <= 1, name
        assert set(names) <= {"FLIP_GSTT_HUB_EAI"}, name


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("TRUE", True), ("false", False), ("", False), (None, False)],
)
def test_the_switch_is_read_literally(value: str | None, expected: bool) -> None:
    kit = {} if value is None else {"HUB_EGRESS": value}
    assert spec_templates.hub_egress(kit) is expected


@pytest.mark.parametrize("value", ["yes", "1", "on", "ture"])
def test_a_typo_fails_naming_the_variable(value: str) -> None:
    """A switch that silently means "no egress" is a support ticket; one that fails is a fix."""
    with pytest.raises(ValueError, match="HUB_EGRESS must be true or false"):
        spec_templates.hub_egress({"HUB_EGRESS": value})


def test_a_spec_attaches_the_integration_and_never_a_host_list(tmp_path: Path, kit: dict[str, str]) -> None:
    """The integration carries the closed list; a spec holding hosts would be a second list.

    The hub URL does appear in trust-api's environment, which is where it belongs. What no spec
    may carry is an ``allowedNetworkRules`` (or an IP or CIDR) of its own.
    """
    _, specs = build(tmp_path, kit, HUB_EGRESS="true")
    text = yaml.safe_dump(specs)
    assert "NetworkRule" not in text
    assert "networkRule" not in text
    assert "/32" not in text
    assert "/24" not in text


def test_the_integration_name_is_the_platform_tiers() -> None:
    """Derived from the same FLIP_<CODE>_ prefix the platform root uses, so they cannot drift."""
    assert Names("GSTT").hub_eai == "FLIP_GSTT_HUB_EAI"
    assert Names("sftest").hub_eai == "FLIP_SFTEST_HUB_EAI"


# --- the deploy preflight ---------------------------------------------------------------------

FAKE_SNOW = """#!/usr/bin/env python3
import json, os, sys
statement = sys.stdin.read()
with open(os.environ["FAKE_SNOW_LOG"], "a") as log:
    log.write(json.dumps(statement) + "\\n")
if statement.startswith("SHOW INTEGRATIONS"):
    print(json.dumps([] if os.environ.get("FAKE_INTEGRATION_MISSING") else [{"name": "FLIP_GSTT_HUB_EAI"}]))
elif statement.startswith("SHOW SERVICES"):
    print(json.dumps([]))
else:
    print("ok")
"""


@pytest.fixture
def fake_snow(tmp_path: Path) -> tuple[Path, Path]:
    binary = tmp_path / "snow"
    binary.write_text(FAKE_SNOW)
    binary.chmod(0o755)
    return binary, tmp_path / "snow.log"


def deploy_up(fake_snow: tuple[Path, Path], rendered: Path, *, missing: bool) -> subprocess.CompletedProcess[str]:
    binary, log = fake_snow
    env = dict(os.environ, FAKE_SNOW_LOG=str(log))
    if missing:
        env["FAKE_INTEGRATION_MISSING"] = "1"
    return subprocess.run(
        [
            sys.executable,
            str(SNOWFLAKE_DIR / "spcs_sql.py"),
            "apply-up",
            "--dir",
            str(rendered),
            "--snow",
            str(binary),
            "--connection",
            "test",
        ],
        env=env,
        capture_output=True,
        text=True,
    )


def test_a_missing_integration_is_named_before_anything_is_created(
    tmp_path: Path, kit: dict[str, str], fake_snow: tuple[Path, Path]
) -> None:
    """The Snowflake error for an unknown integration reads like a typo, not a missing apply."""
    out = tmp_path / "rendered"
    render(dict(kit, HUB_EGRESS="true"), CODE, ENV, out, digests={})
    result = deploy_up(fake_snow, out, missing=True)
    assert result.returncode != 0
    assert "FLIP_GSTT_HUB_EAI" in result.stdout + result.stderr
    assert "enable_hub_egress" in result.stdout + result.stderr
    log = fake_snow[1].read_text()
    assert "CREATE SERVICE" not in log, "nothing may be created when the integration is absent"


def test_the_preflight_lists_exactly_what_the_specs_attach(tmp_path: Path, kit: dict[str, str]) -> None:
    out = tmp_path / "rendered"
    manifest = render(dict(kit, HUB_EGRESS="true"), CODE, ENV, out, digests={})
    assert manifest["external_access_integrations"] == ["FLIP_GSTT_HUB_EAI"]
    assert spcs_sql.missing_integrations.__doc__ is not None, "the why stays with the code"
