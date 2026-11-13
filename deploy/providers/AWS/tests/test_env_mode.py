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
chain. This pins the table itself, the refusal of the retired ``PROD`` selector, the hub env
file and kit-file lookups that still find a file under its pre-FLIP#1479 name, and their
refusal of two candidates; the per-Makefile probes (``test_trust_makefile_hub.py``,
``test_iam_permissions_boundary.py``, ``tests/test_makefile.py``) check the wiring.

Each probe includes a copy of the file in a scratch root, so the hub env files a checkout
may hold never decide a result.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
ENV_MODE_MK = REPO_ROOT / "deploy" / "env_mode.mk"
AWS_DIR = Path(__file__).resolve().parents[1]
#: The Makefiles that read the hub env file, and what each sets before including env_mode.mk.
HUB_ENV_MAKEFILES = (
    "Makefile",
    "deploy/providers/AWS/Makefile",
    "flip-ui/Makefile",
    "fl-services/nvflare/Makefile",
    "fl-services/flower/Makefile",
)
READS = "READS_HUB_ENV_FILE=yes"

_PROBE = """\
.DEFAULT_GOAL := __probe
include deploy/env_mode.mk
__probe:
\t@printf '%s\\0' "$(ENV)" "$(ENV_FILE_NAME)" "$(__DCKR_SUFFIX)" "$(ENV_CLASS)" "$(IS_DEPLOYED)" \
"$(call kit_file,$(DIR),ABC)" "$(call kit_codes,$(DIR))"
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A scratch repo root holding only deploy/env_mode.mk."""
    (tmp_path / "deploy").mkdir()
    shutil.copy(ENV_MODE_MK, tmp_path / "deploy" / "env_mode.mk")
    return tmp_path


def _run(root: Path, *variables: str, probe: str = _PROBE, env: dict[str, str] | None = None):
    return subprocess.run(
        ["make", "-s", "-f", "-", "__probe", *variables],
        input=probe,
        text=True,
        capture_output=True,
        cwd=root,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", **(env or {})},
    )


def _table(root: Path, *variables: str) -> list[str]:
    result = _run(root, *variables)
    assert result.returncode == 0, result.stderr
    return result.stdout.split("\0")[:-1]


@pytest.mark.parametrize(
    ("hub", "env", "compose", "env_class"),
    [
        ("", "development", "development", "stag"),
        ("stag", "stag", "prod", "stag"),
        ("prod", "prod", "prod", "prod"),
    ],
)
def test_hub_table(root: Path, hub: str, env: str, compose: str, env_class: str) -> None:
    assert _table(root, f"HUB={hub}")[:5] == [env, f".env.{env}", compose, env_class, hub]


def test_unknown_hub_value_is_refused(root: Path) -> None:
    """A misspelt HUB must fail at parse time, never fall through to the development shape."""
    result = _run(root, "HUB=production")
    assert result.returncode != 0
    assert "HUB must be unset, stag or prod" in result.stderr


@pytest.mark.parametrize("prod", ["true", "lza", "lza-stag", "stag", "yes"])
def test_any_prod_is_refused_naming_the_hub_equivalent(root: Path, prod: str) -> None:
    result = _run(root, f"PROD={prod}", "HUB=prod")
    assert result.returncode != 0
    assert "unset PROD and pass HUB=prod (was true/lza) or HUB=stag (was lza-stag)" in result.stderr
    assert "PROD=stag's self-contained staging account is retired" in result.stderr


def test_prod_in_the_environment_is_refused_too(root: Path) -> None:
    """Old runbooks said `export PROD=…`, and `sudo -E` keeps it: HUB= alone does not get past it."""
    result = _run(root, "HUB=prod", env={"PROD": "true"})
    assert result.returncode != 0
    assert "unset PROD" in result.stderr


@pytest.mark.parametrize(
    ("hub", "present", "found"),
    [
        ("prod", [".env.ABC.production"], ".env.ABC.production"),
        ("prod", [".env.ABC.lza-prod"], ".env.ABC.lza-prod"),
        ("prod", [".env.ABC"], ".env.ABC"),
        ("prod", [".env.ABC.lza-stag"], ".env.ABC.prod"),
        ("stag", [".env.ABC.lza-stag"], ".env.ABC.lza-stag"),
        ("stag", [".env.ABC.production"], ".env.ABC.stag"),
        ("stag", [".env.ABC"], ".env.ABC.stag"),
        ("", [".env.ABC"], ".env.ABC.development"),
        ("", [".env.ABC.development", ".env.ABC.production"], ".env.ABC.development"),
        ("prod", [], ".env.ABC.prod"),
    ],
)
def test_kit_file_finds_the_current_then_an_old_name(
    root: Path, tmp_path: Path, hub: str, present: list[str], found: str
) -> None:
    for name in present:
        (tmp_path / name).touch()
    assert _table(root, f"HUB={hub}", f"DIR={tmp_path}/")[5] == f"{tmp_path}/{found}"


@pytest.mark.parametrize(
    ("hub", "present"),
    [
        ("prod", [".env.ABC.prod", ".env.ABC.production"]),
        ("prod", [".env.ABC.production", ".env.ABC.lza-prod"]),
        ("prod", [".env.ABC.prod", ".env.ABC"]),
        ("stag", [".env.ABC.stag", ".env.ABC.lza-stag"]),
    ],
)
def test_a_kit_under_two_names_is_refused(root: Path, tmp_path: Path, hub: str, present: list[str]) -> None:
    """Only one of them holds the live kit, and picking either silently could ship the stale one."""
    for name in present:
        (tmp_path / name).touch()
    result = _run(root, f"HUB={hub}", f"DIR={tmp_path}/")
    assert result.returncode != 0
    for name in present:
        assert f"{tmp_path}/{name}" in result.stderr, result.stderr
    assert f"keep the live one as {tmp_path}/.env.ABC.{hub}" in result.stderr, result.stderr


def test_kit_codes_lists_current_names_only(root: Path, tmp_path: Path) -> None:
    """What a sweep loops over: an old name answers only to KIT=, and .env.<word> is no kit."""
    names = [".env.A.prod", ".env.B.production", ".env.C.lza-prod", ".env.D.lza-stag", ".env.E.prod.example"]
    for name in [*names, ".env.F", ".env.local", ".env.example", ".env.G.stag"]:
        (tmp_path / name).touch()
    assert _table(root, "HUB=prod", f"DIR={tmp_path}/")[6] == "A"
    assert _table(root, "HUB=stag", f"DIR={tmp_path}/")[6] == "G"
    assert _table(root, f"DIR={tmp_path}/")[6] == ""


def test_a_sweep_names_the_old_kits_it_skips_in_one_line(root: Path, tmp_path: Path) -> None:
    for name in (".env.A.prod", ".env.B.production", ".env.C.lza-prod", ".env.D.lza-stag", ".env.F"):
        (tmp_path / name).touch()
    probe = f"include deploy/env_mode.mk\n__probe:\n\t@:$(call old_kits_hint,{tmp_path}/)\n"
    assert _run(root, "HUB=prod", probe=probe).stdout.splitlines() == [
        "⚠️  Skipped, still under an old kit name — rename (mv) to include them:"
        " trust/.env.B.production → .env.B.prod trust/.env.C.lza-prod → .env.C.prod"
    ]
    assert _run(root, probe=probe).stdout == ""


@pytest.mark.parametrize(
    ("hub", "present", "message"),
    [
        ("prod", [".env.prod", ".env.lza-prod"], "keep the one holding the live values as .env.prod"),
        ("prod", [".env.production", ".env.lza-prod"], "keep the one holding the live values as .env.prod"),
        ("stag", [".env.stag", ".env.lza-stag"], "keep the one holding the live values as .env.stag"),
        ("prod", [".env.lza-prod"], ".env.lza-prod is the hub env file's pre-FLIP#1479 name: rename it to .env.prod"),
        ("prod", [".env.production"], "rename it to .env.prod"),
        ("stag", [".env.lza-stag"], "rename it to .env.stag"),
    ],
)
def test_an_old_named_hub_env_file_is_refused(root: Path, hub: str, present: list[str], message: str) -> None:
    for name in present:
        (root / name).touch()
    result = _run(root, f"HUB={hub}", READS)
    assert result.returncode != 0
    assert message in result.stderr, result.stderr


@pytest.mark.parametrize(
    ("variables", "present"),
    [
        (("HUB=prod", READS), [".env.prod", ".env.lza-stag", ".env.stag"]),
        ((READS,), [".env.development", ".env.lza-prod", ".env.production"]),
        (("HUB=prod", READS, "MAIN_ENV_FILE=.env.prod"), [".env.prod", ".env.lza-prod"]),
        # The trust-side Makefiles never read a hub env file, so an admin box's old one must not stop them.
        (("HUB=prod",), [".env.prod", ".env.lza-prod"]),
    ],
)
def test_the_hub_env_check_stays_out_of_the_way(root: Path, variables: tuple[str, ...], present: list[str]) -> None:
    """Another hub's files, development, a MAIN_ENV_FILE named on the command line, or a Makefile
    that reads no hub env file pass."""
    for name in present:
        (root / name).touch()
    assert _run(root, *variables).returncode == 0


def test_an_exported_main_env_file_does_not_switch_the_check_off(root: Path) -> None:
    """The Makefiles load .env.<hub> whatever the environment says, so the environment cannot vouch for it."""
    for name in (".env.prod", ".env.lza-prod"):
        (root / name).touch()
    result = _run(root, "HUB=prod", READS, env={"MAIN_ENV_FILE": ".env.prod"})
    assert result.returncode != 0
    assert "keep the one holding the live values as .env.prod" in result.stderr, result.stderr


@pytest.mark.parametrize("makefile", HUB_ENV_MAKEFILES)
def test_a_makefile_that_reads_the_hub_env_file_opts_in_and_exports_real_keys_only(makefile: str) -> None:
    """`sed 's/=.*//'` also emits `# KEY=`, and a bare `export KEY` defines KEY empty, defeating `KEY ?=`."""
    text = (REPO_ROOT / makefile).read_text()
    assert re.search(r"^READS_HUB_ENV_FILE :=.*\ninclude .*env_mode\.mk$", text, re.M), makefile
    assert "export $(shell sed -n 's/^[[:space:]]*\\([A-Za-z_][A-Za-z0-9_]*\\)=.*/\\1/p'" in text, makefile
    assert "export $(shell sed 's/=.*//'" not in text, makefile


def test_kit_hint_names_the_rename(root: Path) -> None:
    probe = """\
include deploy/env_mode.mk
__probe:
\t@:$(call kit_hint,trust/.env.ABC.lza-prod)$(call kit_hint,trust/.env.XYZ)
\t@:$(call kit_hint,trust/.env.OK.prod)
"""
    result = _run(root, "HUB=prod", probe=probe)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "⚠️  trust/.env.ABC.lza-prod is an old kit name — rename (mv) it to trust/.env.ABC.prod",
        "⚠️  trust/.env.XYZ is an old kit name — rename (mv) it to trust/.env.XYZ.prod",
    ]


def test_kit_hint_prints_once_and_never_for_a_kit_file_the_user_named(root: Path) -> None:
    probe = """\
include deploy/env_mode.mk
__probe:
\t@:$(call kit_hint,trust/.env.ABC.lza-prod)$(call kit_hint,trust/.env.ABC.lza-prod)
\t@echo "$$_KIT_HINTED"
"""
    once = _run(root, "HUB=prod", probe=probe)
    assert once.stdout.splitlines() == [
        "⚠️  trust/.env.ABC.lza-prod is an old kit name — rename (mv) it to trust/.env.ABC.prod",
        "trust/.env.ABC.lza-prod",  # exported, so a sub-make does not repeat it
    ]
    assert "old kit name" not in _run(root, "HUB=prod", "KIT_FILE=x", probe=probe).stdout


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


#: A comparison of HUB against a value it can no longer take: make (`ifeq ($(HUB),x)`,
#: `$(filter x,$(HUB))`), shell or recipe (`"$HUB" = x`, `"$(HUB)" = x`) and Python (`HUB") == "x"`).
_OLD_HUB_COMPARISON = re.compile(
    r"\(\$\(HUB\),\s*(?:true|production|lza)"
    r"|\$\(filter\s+(?:true|production|lza)\S*,\s*\$\(HUB\)\)"
    r"|\$[({]?HUB(?::-[^})]*)?[)}]?\"?\s*!?==?\s*\"?(?:true|production|lza)"
    r"|HUB[\"']\)?\s*[!=]=\s*[\"'](?:true|production|lza)"
)


def test_no_makefile_or_script_compares_hub_to_an_old_value() -> None:
    """HUB is stag, prod or unset: a branch on `true`, `production` or `lza` is dead code."""
    tracked = subprocess.run(
        ["git", "ls-files", "*Makefile", "*.mk", "*.sh", "*.py"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.split()
    hits = [
        f"{name}:{number}: {line.strip()}"
        for name in tracked
        if (REPO_ROOT / name).is_file()
        for number, line in enumerate((REPO_ROOT / name).read_text(errors="replace").splitlines(), 1)
        if _OLD_HUB_COMPARISON.search(line)
    ]
    assert not hits, "\n".join(hits)
