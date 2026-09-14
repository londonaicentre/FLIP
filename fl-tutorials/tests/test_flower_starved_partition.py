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
# A starved partition must fail naming cohort size, not something else.
#
# partition_cohort() slices the shared dev cohort per simulated site (FLIP#1157), so a cohort too
# small for the site count leaves one site with nothing. What happens next is up to the app, and
# the evaluation tutorial's next step is get_test_data_list(), which refuses an empty datalist with
# a message about data enrichment ("0 accession(s), none with a matching label_*.nii.gz") — the
# wrong cause, sending the researcher to XNAT when the fix is more cases or fewer sites. The guard
# (check_splits_are_populated) therefore has to run BEFORE the datalist is built, and that ordering
# is what this pins: the datalist builder is never reached.
#
# In-process and CPU-only: the handler raises before it touches a device, a model or a file. The
# client_app is imported for real — flwr, torch and MONAI are in flip-utils[full], this suite's
# environment — so the test drives the shipped handler rather than a re-implementation of it.

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest
from flip.flower import identity

REPO_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_TUTORIAL = REPO_ROOT / "fl-tutorials" / "flower" / "3d_spleen_segmentation_evaluation"


def _app_modules() -> dict[str, ModuleType]:
    return {name: module for name, module in sys.modules.items() if name == "app" or name.startswith("app.")}


@pytest.fixture
def evaluation_client_app(monkeypatch: pytest.MonkeyPatch):
    """The evaluation tutorial's client_app, imported under the ``app`` package name it ships as.

    Every tutorial ships its code as a package called ``app``, so whichever one is imported first
    would otherwise be served to the next; the fixture clears that name before and after.
    """
    displaced = _app_modules()
    for name in displaced:
        del sys.modules[name]
    monkeypatch.syspath_prepend(str(EVALUATION_TUTORIAL))
    try:
        yield importlib.import_module("app.client_app")
    finally:
        for name in _app_modules():
            del sys.modules[name]
        sys.modules.update(displaced)


class _OneRowFlipBase:
    """FLIP_BASE stand-in serving a one-row cohort, which records whether the datalist was built."""

    def __init__(self) -> None:
        self.project_id = ""
        self.query = ""
        self.dataframe: pd.DataFrame | None = None
        self.flip = SimpleNamespace(get_dataframe=lambda project_id, query: pd.DataFrame({"accession_id": ["ACC-1"]}))
        self.datalist_built = False

    def get_test_data_list(self) -> list[dict[str, str]]:
        self.datalist_built = True
        raise AssertionError("get_test_data_list() was reached — the starved-partition guard did not run first")


def _context(partition_id: int) -> SimpleNamespace:
    # Simulation writes node_config values as strings; the app must accept them as such.
    return SimpleNamespace(run_config={}, node_config={"partition-id": str(partition_id), "num-partitions": "2"})


def test_starved_partition_fails_naming_cohort_size(evaluation_client_app, monkeypatch: pytest.MonkeyPatch):
    """Two simulated sites, one accession: the site that draws nothing must say so, before any I/O."""
    monkeypatch.setattr(identity, "FlipConstants", SimpleNamespace(LOCAL_DEV=True))
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    flip_base = _OneRowFlipBase()
    monkeypatch.setattr(evaluation_client_app, "FLIP_BASE", lambda: flip_base)

    # Whichever site the single row does not hash to is the starved one — derived from the same
    # split the handler performs, so the test cannot drift from the bucketing rule.
    cohort = flip_base.flip.get_dataframe(project_id="", query="")
    starved = next(pid for pid in (0, 1) if identity.partition_cohort(cohort, _context(pid)).empty)

    with pytest.raises(ValueError, match="too small to split") as raised:
        evaluation_client_app.evaluate(SimpleNamespace(content={}), _context(starved))

    message = str(raised.value)
    assert f"site-{starved + 1}: split test is empty" in message
    assert "after partitioning a shared cohort 2 ways" in message
    assert "label_" not in message, "the enrichment message leaked through — the guard ran too late"
    assert not flip_base.datalist_built


def test_populated_partition_reaches_the_datalist(evaluation_client_app, monkeypatch: pytest.MonkeyPatch):
    """The guard is a gate, not a wall: the site holding the row proceeds to build its datalist."""
    monkeypatch.setattr(identity, "FlipConstants", SimpleNamespace(LOCAL_DEV=True))
    monkeypatch.delenv("SUPERNODE_NAME", raising=False)
    flip_base = _OneRowFlipBase()
    monkeypatch.setattr(evaluation_client_app, "FLIP_BASE", lambda: flip_base)

    cohort = flip_base.flip.get_dataframe(project_id="", query="")
    populated = next(pid for pid in (0, 1) if not identity.partition_cohort(cohort, _context(pid)).empty)

    # The stand-in's datalist builder raises AssertionError on entry, which here means the guard
    # let the populated site through to it.
    with pytest.raises(AssertionError, match="get_test_data_list\\(\\) was reached"):
        evaluation_client_app.evaluate(SimpleNamespace(content={}), _context(populated))
    assert flip_base.datalist_built
