#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
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
"""One table for every reader of the kit's TRUST_DATA_SEED (FLIP#1687).

deploy/env_mode.mk's trust_data_seed (a kit make includes: trust/Makefile) and kit_trust_data_seed (a kit read
with sed: the Helm and EC2 Makefiles), and trust_kit_lib.trust_data_seed (the onboarding checklist, sync-kit and
the node self-test's kit), must read each kit line alike: a reader that took a value as true where another took
it as false would seed a real trust's OMOP.

Usage:
    uv run --no-config trust/deploy/scripts/tests/test_trust_data_seed.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SCRIPTS_DIR.parents[2]
sys.path.insert(0, str(SCRIPTS_DIR))

import trust_kit_lib  # noqa: E402

# The kit line (None: no line at all) and what every reader makes of it; None: refused.
TABLE: list[tuple[str | None, bool | None]] = [
    (None, True),
    ("TRUST_DATA_SEED=", True),
    ("TRUST_DATA_SEED=true", True),
    ("TRUST_DATA_SEED=false", False),
    ('TRUST_DATA_SEED="false"', False),
    ("TRUST_DATA_SEED='false'", False),
    ("TRUST_DATA_SEED=false  # real trust", False),
    ('TRUST_DATA_SEED="false" # real trust', False),
    ('TRUST_DATA_SEED=fal"se', False),
    ("TRUST_DATA_SEED= false ", False),
    ("TRUST_DATA_SEED=true\nTRUST_DATA_SEED=false", False),
    ("TRUST_DATA_SEED=False", None),
    ("TRUST_DATA_SEED=no", None),
    ("TRUST_DATA_SEED=tr ue", None),
    ('TRUST_DATA_SEED="fal#se"', None),
]

PASS = 0
FAIL = 0


def _assert(condition: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}")
        for line in detail.splitlines():
            print(f"    {line}")
        FAIL += 1


def _verdict(value: str) -> bool | None:
    return {"true": True, "false": False}.get(value)


def _python(line: str | None) -> bool | None:
    kit: dict[str, str] = {}
    for raw in (line or "").splitlines():  # read_kit_vars' shape: the last line wins
        key, _, value = raw.partition("=")
        kit[key] = value
    try:
        return trust_kit_lib.trust_data_seed(kit)
    except ValueError:
        return None


def _make(line: str | None, tmp: Path) -> tuple[str, str]:
    """(included, sed): what make's two readers make of a kit holding ``line``."""
    kit = tmp / "kit"
    kit.write_text("OTHER=1\n" + (line + "\n" if line is not None else ""))
    probe = tmp / "probe.mk"
    probe.write_text(
        f"include {REPO_ROOT}/deploy/env_mode.mk\n"
        f"include {kit}\n"
        "probe:\n"
        f"\t@: $(info [$(call trust_data_seed,$(TRUST_DATA_SEED))][$(call kit_trust_data_seed,{kit})])\n"
    )
    out = subprocess.run(
        ["make", "--no-print-directory", "-s", "-f", str(probe), "probe"],
        capture_output=True,
        text=True,
        cwd=tmp,
        timeout=30,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    included, _, sed = out.stdout.strip().removeprefix("[").removesuffix("]").partition("][")
    return included, sed


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for line, expected in TABLE:
            included, sed = _make(line, tmp)
            got = (_python(line), _verdict(included), _verdict(sed))
            _assert(
                got == (expected, expected, expected),
                f"{line!r} -> {expected}",
                f"python={got[0]} make-included={included!r} make-sed={sed!r}",
            )
    print(f"\n==== {PASS} passed, {FAIL} failed ====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
