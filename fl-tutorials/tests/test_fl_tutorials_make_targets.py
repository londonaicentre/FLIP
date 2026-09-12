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
# Every `make -C fl-tutorials <target>` the docs quote must resolve at the fl-tutorials root.
#
# The root Makefile owns no dataset or tutorial recipes: it forwards a fixed list of target
# names to datasets/ or to the FL_BACKEND harness. A target added to datasets/Makefile and
# documented in the root form therefore fails with "No rule to make target" until someone also
# adds it to the forwarding list — which is exactly what happened to download-spleen-checkpoint
# (FLIP#1159 review). The docs are the contract, so they are what this test reads.

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

FL_TUTORIALS = Path(__file__).resolve().parents[1]
REPO_ROOT = FL_TUTORIALS.parent
DOC_SOURCES = [
    REPO_ROOT / "CLAUDE.md",
    *FL_TUTORIALS.rglob("README.md"),
]
_DOCUMENTED = re.compile(r"make -C fl-tutorials ([a-z][a-z0-9-]*)")


def _documented_root_targets() -> list[str]:
    targets: set[str] = set()
    for doc in DOC_SOURCES:
        if ".venv" in doc.parts or "data" in doc.relative_to(REPO_ROOT).parts[:2]:
            continue
        targets.update(_DOCUMENTED.findall(doc.read_text(encoding="utf-8")))
    assert targets, "no `make -C fl-tutorials <target>` lines found in the docs"
    return sorted(targets)


@pytest.mark.parametrize("target", _documented_root_targets())
def test_documented_root_target_resolves(target: str):
    # -n prints recipes instead of running them; a missing target still fails at rule lookup.
    # Recursive $(MAKE) lines run under -n too, so a forwarded name is checked in its owner.
    result = subprocess.run(
        ["make", "-n", "-C", str(FL_TUTORIALS), target], capture_output=True, text=True, timeout=60
    )
    assert "No rule to make target" not in result.stderr, result.stderr
