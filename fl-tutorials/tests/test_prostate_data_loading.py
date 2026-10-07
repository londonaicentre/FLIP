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

"""Pin FLIP_BASE.get_case_list's trust-loading behaviour: pull once per accession (not per cohort
row), pick the requested series out of the three a prostate study pulls (t2w + adc + hbv sharing one
accession_id) by the signals XNAT's export layout carries, and refuse to guess when none of them
singles a file out.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from tutorial_apps import TUTORIALS_ROOT

PROSTATE_DIR = TUTORIALS_ROOT / "flower" / "3d_prostate_segmentation"
# XNAT's export tree for one pulled accession: one scan folder per series, named <scan id>-<description>
# where the scan id is the DICOM SeriesNumber convert_mha_to_dicom.py stamps (t2w 1, adc 2, hbv 3).
PLATFORM_SCANS = {"t2w": "1-T2_Weighted", "adc": "2-ADC_Map", "hbv": "3-High_B-Value_DWI"}


def _app_modules() -> dict[str, ModuleType]:
    return {name: module for name, module in sys.modules.items() if name == "app" or name.startswith("app.")}


@pytest.fixture(scope="module")
def data_loading_module() -> ModuleType:
    """``app.data_loading`` imported under the ``app`` package name the tutorial ships as.

    Every tutorial ships its code as a package called ``app``, so whichever one is imported first
    would otherwise be served to the next; the fixture clears that name before and after.
    """
    displaced = _app_modules()
    for name in displaced:
        del sys.modules[name]
    sys.path.insert(0, str(PROSTATE_DIR))
    try:
        yield importlib.import_module("app.data_loading")
    finally:
        sys.path.remove(str(PROSTATE_DIR))
        for name in _app_modules():
            del sys.modules[name]
        sys.modules.update(displaced)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def _platform_accession(root: Path, accession: str, scan_id_by_modality: dict[str, str], *, masks: bool = True) -> Path:
    """The pulled tree: <accession>/<session>/scans/<scan>/resources/NIFTI/files/{input,label,zonal}_<X>.nii.gz."""
    site = root / accession
    for index, (modality, scan) in enumerate(scan_id_by_modality.items(), start=10):
        files = site / f"{accession}_session" / "scans" / scan / "resources" / "NIFTI" / "files"
        # XNAT's dcm2niix names the image without the modality; the enrichment upload mirrors that stem.
        _touch(files / f"input_{accession}_{index}.nii.gz")
        if masks:
            _touch(files / f"label_{accession}_{index}.nii.gz")
            _touch(files / f"zonal_{accession}_{index}.nii.gz")
        del modality
    return site


def _flip_base(module: ModuleType, accession_dirs: dict[str, Path], rows: list[str]):
    flip_base = module.FLIP_BASE()
    flip_base.project_id = "proj"
    flip_base.dataframe = pd.DataFrame({"accession_id": rows})
    flip_base.flip = MagicMock()
    flip_base.flip.get_by_accession_number.side_effect = lambda _project, accession, **_kw: accession_dirs[accession]
    return flip_base


def test_pulls_each_accession_once_despite_one_row_per_modality(
    data_loading_module: ModuleType, tmp_path: Path
) -> None:
    """query.sql yields 3 rows (t2w/adc/hbv) per study sharing one accession_id; the pull must dedupe."""
    site = _platform_accession(tmp_path, "acc1", PLATFORM_SCANS)
    flip_base = _flip_base(data_loading_module, {"acc1": site}, ["acc1", "acc1", "acc1"])

    train, val = flip_base.get_case_list(modality="t2w", val_split=0.0, test_split=0.0)

    assert flip_base.flip.get_by_accession_number.call_count == 1
    assert len(train) == 1
    assert val == []
    assert train[0]["accession_id"] == "acc1"


@pytest.mark.parametrize("modality", ["t2w", "adc", "hbv"])
def test_selects_the_requested_series_by_scan_folder(
    data_loading_module: ModuleType, tmp_path: Path, modality: str
) -> None:
    """On the platform the scan folder's leading number is the SeriesNumber — the primary signal."""
    site = _platform_accession(tmp_path, "acc1", PLATFORM_SCANS)
    flip_base = _flip_base(data_loading_module, {"acc1": site}, ["acc1"])

    (train,), _ = flip_base.get_case_list(modality=modality, val_split=0.0, test_split=0.0)

    image = train[data_loading_module.IMAGE_KEY]
    assert PLATFORM_SCANS[modality] in image.parts
    assert image.name.startswith("input_")
    assert train[data_loading_module.WHOLE_GLAND_KEY] == image.with_name(image.name.replace("input_", "label_"))
    assert train[data_loading_module.PZ_TZ_KEY] == image.with_name(image.name.replace("input_", "zonal_"))
    assert train[data_loading_module.WHOLE_GLAND_KEY].exists()


def test_selects_by_file_name_token_when_folders_carry_no_series_number(
    data_loading_module: ModuleType, tmp_path: Path
) -> None:
    """The local conversion's naming: input_<accession>_<modality>.nii.gz, flat under scans/."""
    site = tmp_path / "acc1"
    for modality in ("t2w", "adc", "hbv"):
        for prefix in ("input", "label", "zonal"):
            _touch(site / "scans" / f"{prefix}_acc1_{modality}.nii.gz")
    flip_base = _flip_base(data_loading_module, {"acc1": site}, ["acc1"])

    (train,), _ = flip_base.get_case_list(modality="adc", val_split=0.0, test_split=0.0)

    assert train[data_loading_module.IMAGE_KEY] == site / "scans" / "input_acc1_adc.nii.gz"


def test_scan_folder_wins_over_a_contradicting_file_name(data_loading_module: ModuleType) -> None:
    """The two signals disagree → the platform's own scan numbering is trusted."""
    select_series = data_loading_module.select_series
    root = Path("/virtual")
    paths = [
        root / "s" / "scans" / "1-T2_Weighted" / "input_x_adc.nii.gz",
        root / "s" / "scans" / "2-ADC_Map" / "input_x_t2w.nii.gz",
    ]
    with patch.object(Path, "rglob", return_value=iter(paths)):
        assert select_series(root, "t2w") == paths[0]


def test_single_unnamed_input_is_taken(data_loading_module: ModuleType, tmp_path: Path) -> None:
    site = tmp_path / "acc1"
    _touch(site / "input_5.nii.gz")
    _touch(site / "label_5.nii.gz")
    _touch(site / "zonal_5.nii.gz")
    flip_base = _flip_base(data_loading_module, {"acc1": site}, ["acc1"])

    (train,), _ = flip_base.get_case_list(modality="t2w", val_split=0.0, test_split=0.0)

    assert train[data_loading_module.IMAGE_KEY] == site / "input_5.nii.gz"


def test_refuses_to_guess_among_unidentifiable_scans(data_loading_module: ModuleType, tmp_path: Path) -> None:
    """Three input_*.nii.gz with neither a numbered scan folder nor a modality token → raise, listing them."""
    site = tmp_path / "acc2"
    for scan_id in (10, 11, 12):
        _touch(site / f"input_{scan_id}.nii.gz")
    flip_base = _flip_base(data_loading_module, {"acc2": site}, ["acc2"])

    with pytest.raises(RuntimeError, match=r"3 input_\*\.nii\.gz.*input_10.*input_11.*input_12"):
        flip_base.get_case_list(modality="t2w", val_split=0.0, test_split=0.0)


def test_ambiguous_signal_raises(data_loading_module: ModuleType, tmp_path: Path) -> None:
    """Two scans both numbered 1-* is a broken pull, not a coin toss."""
    site = tmp_path / "acc1"
    _touch(site / "scans" / "1-T2_Weighted" / "input_a.nii.gz")
    _touch(site / "scans" / "1-T2_Weighted_repeat" / "input_b.nii.gz")

    with pytest.raises(RuntimeError, match="scan folder 1-\\* matches 2 scans"):
        data_loading_module.select_series(site, "t2w")


def test_unknown_modality_is_rejected(data_loading_module: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown modality"):
        data_loading_module.select_series(tmp_path, "dwi")


def test_skips_accession_missing_a_label(data_loading_module: ModuleType, tmp_path: Path) -> None:
    """No label uploaded yet (data enrichment not run) -> skipped, then the zero-cases guard names the cause."""
    site = _platform_accession(tmp_path, "acc3", PLATFORM_SCANS, masks=False)
    flip_base = _flip_base(data_loading_module, {"acc3": site}, ["acc3"])

    with pytest.raises(RuntimeError, match="No usable cases"):
        flip_base.get_case_list(modality="t2w", val_split=0.0, test_split=0.0)


def test_skips_empty_accession_dir(data_loading_module: ModuleType, tmp_path: Path) -> None:
    """LOCAL_DEV creates a blank folder for an accession it has no data for — skipped, not raised."""
    empty = tmp_path / "acc4"
    empty.mkdir()
    good = _platform_accession(tmp_path, "acc5", PLATFORM_SCANS)
    flip_base = _flip_base(data_loading_module, {"acc4": empty, "acc5": good}, ["acc4", "acc5"])

    train, _ = flip_base.get_case_list(modality="t2w", val_split=0.0, test_split=0.0)

    assert [case["accession_id"] for case in train] == ["acc5"]


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
