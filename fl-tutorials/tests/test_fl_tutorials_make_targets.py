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
    REPO_ROOT / "AGENTS.md",
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
    result = subprocess.run(["make", "-n", "-C", str(FL_TUTORIALS), target], capture_output=True, text=True, timeout=60)
    assert "No rule to make target" not in result.stderr, result.stderr


# The tutorial targets mean the same thing on both backends (FLIP#1435): run-tutorial and
# run-all-tutorials — the pre-release tutorial check — run the simulator, and sim-tutorial is an
# alias. On Flower that is sim-tutorial.sh; the compose-stack runner it replaced must not come back
# under these names, or the release check would silently run something else on one backend.
_TARGETS_LINE = re.search(r"^TUTORIAL_TARGETS := (.+)$", (FL_TUTORIALS / "Makefile").read_text(encoding="utf-8"), re.M)
assert _TARGETS_LINE, "fl-tutorials/Makefile no longer defines TUTORIAL_TARGETS on one `:=` line; update this test"
_TUTORIAL_TARGETS = _TARGETS_LINE.group(1).split()


@pytest.mark.parametrize("backend", ["nvflare", "flower"])
@pytest.mark.parametrize("target", _TUTORIAL_TARGETS)
def test_tutorial_target_resolves_on_both_backends(target: str, backend: str):
    result = subprocess.run(
        ["make", "-n", "-C", str(FL_TUTORIALS), target, f"FL_BACKEND={backend}", "TUTORIAL=xray_classification"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "No rule to make target" not in result.stderr, result.stderr


@pytest.mark.parametrize("target", ["run-tutorial", "sim-tutorial", "run-all-tutorials"])
def test_flower_tutorial_targets_run_the_simulator(target: str):
    result = subprocess.run(
        ["make", "-n", "-C", str(FL_TUTORIALS), target, "FL_BACKEND=flower", "TUTORIAL=xray_classification"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "flower/sim-tutorial.sh" in result.stdout, result.stdout
    # The compose runner this replaced was `bash .../flower/run-tutorial.sh` (FLIP#1435).
    assert "run-tutorial.sh" not in result.stdout, result.stdout
