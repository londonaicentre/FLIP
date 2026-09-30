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

"""Pin FLIP_BASE.get_case_list's trust-loading behaviour: pull once per accession (not per cohort
row), and refuse to guess a modality rather than silently picking a file — see data_loading.py's
own docstring for why a prostate accession (t2w+adc+hbv sharing one accession_id) can't be
resolved yet.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pandas as pd
import pytest
from tutorial_apps import TUTORIALS_ROOT

PROSTATE_DIR = TUTORIALS_ROOT / "flower" / "3d_prostate_segmentation"


@pytest.fixture(scope="module")
def data_loading_module() -> ModuleType:
    """``data_loading.py`` loaded from its path — a loose script, not a package.

    It does a bare ``from dataset import ...``, so its own directory has to be on sys.path for
    that import to resolve (unlike dataset.py itself, which only imports third-party packages).
    """
    sys.path.insert(0, str(PROSTATE_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "prostate_tutorial_data_loading", PROSTATE_DIR / "data_loading.py"
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(PROSTATE_DIR))


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def test_pulls_each_accession_once_despite_one_row_per_modality(
    data_loading_module: ModuleType, tmp_path: Path
) -> None:
    """query.sql yields 3 rows (t2w/adc/hbv) per study sharing one accession_id; the pull must dedupe."""
    site = tmp_path / "acc1"
    _touch(site / "input_5.nii.gz")
    _touch(site / "label_5.nii.gz")
    _touch(site / "zonal_5.nii.gz")

    flip_base = data_loading_module.FLIP_BASE()
    flip_base.project_id = "proj"
    flip_base.dataframe = pd.DataFrame({"accession_id": ["acc1", "acc1", "acc1"]})
    flip_base.flip = MagicMock()
    flip_base.flip.get_by_accession_number.return_value = site

    train, val = flip_base.get_case_list(modality="t2w", val_split=0.0, test_split=0.0)

    assert flip_base.flip.get_by_accession_number.call_count == 1
    assert len(train) == 1
    assert val == []
    assert train[0]["accession_id"] == "acc1"
    assert train[0][data_loading_module.IMAGE_KEY] == site / "input_5.nii.gz"
    assert train[0][data_loading_module.WHOLE_GLAND_KEY] == site / "label_5.nii.gz"
    assert train[0][data_loading_module.PZ_TZ_KEY] == site / "zonal_5.nii.gz"


def test_refuses_to_guess_among_multiple_scans(data_loading_module: ModuleType, tmp_path: Path) -> None:
    """A real prostate accession pulls back 3 input_*.nii.gz (t2w/adc/hbv) — must raise, not guess."""
    site = tmp_path / "acc2"
    for scan_id in (10, 11, 12):
        _touch(site / f"input_{scan_id}.nii.gz")

    flip_base = data_loading_module.FLIP_BASE()
    flip_base.project_id = "proj"
    flip_base.dataframe = pd.DataFrame({"accession_id": ["acc2"]})
    flip_base.flip = MagicMock()
    flip_base.flip.get_by_accession_number.return_value = site

    with pytest.raises(RuntimeError, match="pulled 3 input_"):
        flip_base.get_case_list(modality="t2w", val_split=0.0, test_split=0.0)


def test_skips_accession_missing_a_label(data_loading_module: ModuleType, tmp_path: Path) -> None:
    """No label uploaded yet (data enrichment not run) -> skipped, not a crash."""
    site = tmp_path / "acc3"
    _touch(site / "input_1.nii.gz")  # no label_1.nii.gz / zonal_1.nii.gz

    flip_base = data_loading_module.FLIP_BASE()
    flip_base.project_id = "proj"
    flip_base.dataframe = pd.DataFrame({"accession_id": ["acc3"]})
    flip_base.flip = MagicMock()
    flip_base.flip.get_by_accession_number.return_value = site

    with pytest.raises(RuntimeError, match="No usable cases"):
        flip_base.get_case_list(modality="t2w", val_split=0.0, test_split=0.0)


def test_get_case_list_before_fetch_dataframe_raises(data_loading_module: ModuleType) -> None:
    flip_base = data_loading_module.FLIP_BASE()
    with pytest.raises(RuntimeError, match="fetch_dataframe"):
        flip_base.get_case_list(modality="t2w", val_split=0.0, test_split=0.0)


def test_fetch_dataframe_calls_flip_with_project_and_query(data_loading_module: ModuleType) -> None:
    flip_base = data_loading_module.FLIP_BASE()
    flip_base.flip = MagicMock()
    flip_base.flip.get_dataframe.return_value = pd.DataFrame({"accession_id": []})
    flip_base.project_id = "proj-123"
    flip_base.query = "SELECT 1"

    flip_base.fetch_dataframe()

    flip_base.flip.get_dataframe.assert_called_once_with(project_id="proj-123", query="SELECT 1")
