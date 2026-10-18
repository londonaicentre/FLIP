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

"""Pin the simulator layout prepare_prostate_local_data.py writes from a PI-CAI download.

It has to look like a platform pull — one XNAT scan folder per series, named by the DICOM
SeriesNumber, the two enrichment masks beside every image — so the tutorial's series selection and
mask pairing run the same code in the simulator as on a trust. The voxels and affine must survive
the SimpleITK round trip, and the dataframe must carry query.sql's columns, three rows per study.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import nibabel as nib
import numpy as np
import pytest
import SimpleITK as sitk

PROSTATE_DIR = Path(__file__).resolve().parents[3] / "datasets" / "prostate"


def load_script(name: str) -> ModuleType:
    """A loose script from ``datasets/prostate/`` loaded from its path, siblings importable."""
    if str(PROSTATE_DIR) not in sys.path:
        sys.path.insert(0, str(PROSTATE_DIR))
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", PROSTATE_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def prepare() -> ModuleType:
    return load_script("prepare_prostate_local_data")


CASES = [("10000", "1000000", "ZGT"), ("10001", "1000001", "PCNN"), ("10002", "1000002", "RUMC")]


def _write_mha(path: Path, seed: int, shape=(6, 8, 4)) -> np.ndarray:
    """A small volume with a non-trivial origin/spacing so the affine has something to preserve."""
    rng = np.random.default_rng(seed)
    array = rng.integers(0, 1000, size=shape[::-1]).astype(np.float32)  # sitk arrays are (z, y, x)
    image = sitk.GetImageFromArray(array)
    image.SetSpacing((0.5, 0.5, 3.0))
    image.SetOrigin((10.0 + seed, -20.0, 5.0))
    path.parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(image, str(path))
    return array


def _write_mask(path: Path, seed: int, values, shape=(6, 8, 4)) -> None:
    rng = np.random.default_rng(seed)
    array = rng.choice(values, size=shape[::-1]).astype(np.uint8)
    image = sitk.GetImageFromArray(array)
    image.SetSpacing((0.5, 0.5, 3.0))
    image.SetOrigin((10.0 + seed, -20.0, 5.0))
    path.parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(image, str(path))


@pytest.fixture
def download(tmp_path: Path) -> Path:
    """A three-study PI-CAI download: images/<p>/<p>_<s>_<mod>.mha, labels/, zonal_labels/, marksheet."""
    data = tmp_path / "prostate"
    for index, (patient, study, _center) in enumerate(CASES):
        accession = f"{patient}_{study}"
        for modality in ("t2w", "adc", "hbv"):
            _write_mha(data / "images" / patient / f"{accession}_{modality}.mha", seed=index)
        _write_mask(data / "labels" / f"{accession}.nii.gz", seed=index, values=[0, 1])
        _write_mask(data / "zonal_labels" / f"{accession}.nii.gz", seed=index, values=[0, 1, 2])
    # A fourth study with no zonal mask must be left out.
    _write_mha(data / "images" / "10003" / "10003_1000003_t2w.mha", seed=9)
    _write_mha(data / "images" / "10003" / "10003_1000003_adc.mha", seed=9)
    _write_mha(data / "images" / "10003" / "10003_1000003_hbv.mha", seed=9)
    _write_mask(data / "labels" / "10003_1000003.nii.gz", seed=9, values=[0, 1])
    sheet = data / "clinical_information" / "marksheet.csv"
    sheet.parent.mkdir(parents=True)
    with sheet.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["patient_id", "study_id", "mri_date", "patient_age", "center", "case_ISUP", "case_csPCa", "lesion_PIRADS"]
        )
        for (patient, study, center), isup, cspca, pirads in zip(
            CASES, ["0", "2", "3"], ["NO", "YES", "YES"], ["N/A", "3,4", "5"], strict=True
        ):
            writer.writerow([patient, study, "2020-01-0" + patient[-1], "65", center, isup, cspca, pirads])
    return data


def test_select_cases_requires_every_scan_and_both_masks(prepare: ModuleType, download: Path) -> None:
    cases = prepare.select_cases(download / "images", download / "labels", download / "zonal_labels")
    assert cases == ["10000_1000000", "10001_1000001", "10002_1000002"]
    assert (
        prepare.select_cases(download / "images", download / "labels", download / "zonal_labels", num_cases=2)
        == cases[:2]
    )


def test_layout_is_the_xnat_export_shape(prepare: ModuleType, download: Path) -> None:
    prepare.prepare(download, download, ["10000_1000000"])

    scans = download / "images" / "10000_1000000" / "scans"
    assert sorted(p.name for p in scans.iterdir()) == ["1-T2_Weighted", "2-ADC_Map", "3-High_B-Value_DWI"]
    for folder, modality in (("1-T2_Weighted", "t2w"), ("2-ADC_Map", "adc"), ("3-High_B-Value_DWI", "hbv")):
        files = sorted(p.name for p in (scans / folder).iterdir())
        assert files == [
            f"input_10000_1000000_{modality}.nii.gz",
            f"label_10000_1000000_{modality}.nii.gz",
            f"zonal_10000_1000000_{modality}.nii.gz",
        ]


def test_image_voxels_and_grid_survive_the_round_trip(prepare: ModuleType, download: Path) -> None:
    prepare.prepare(download, download, ["10001_1000001"])
    scan = download / "images" / "10001_1000001" / "scans" / "1-T2_Weighted"

    source = sitk.ReadImage(str(download / "images" / "10001" / "10001_1000001_t2w.mha"))
    written = nib.load(str(scan / "input_10001_1000001_t2w.nii.gz"))
    mask = nib.load(str(scan / "label_10001_1000001_t2w.nii.gz"))

    # nibabel reads (x, y, z); sitk arrays are (z, y, x)
    np.testing.assert_array_equal(np.asanyarray(written.dataobj), sitk.GetArrayFromImage(source).transpose(2, 1, 0))
    np.testing.assert_allclose(np.abs(np.diag(written.affine)[:3]), (0.5, 0.5, 3.0))
    np.testing.assert_allclose(written.affine, mask.affine, err_msg="image and mask describe one grid")


def test_dataframe_has_three_rows_per_study_with_query_columns(prepare: ModuleType, download: Path) -> None:
    rows = prepare.prepare(download, download, ["10000_1000000", "10001_1000001"])

    with (download / "dataframe.csv").open() as handle:
        table = list(csv.DictReader(handle))
    assert list(table[0]) == prepare.DATAFRAME_COLUMNS
    assert [r["accession_id"] for r in table] == ["10000_1000000"] * 3 + ["10001_1000001"] * 3
    assert len({r["image_series_uid"] for r in table}) == 6, "one deterministic SeriesInstanceUID per series"
    assert all(r["Modality"] == "Magnetic resonance imaging" and r["Anatomy"] == "Prostatic structure" for r in table)
    first, second = table[0], table[3]
    assert (first["ISUP grade group"], first["Clinically significant cancer"], first["PI-RADS"]) == ("0", "No", "")
    assert (second["ISUP grade group"], second["Clinically significant cancer"], second["PI-RADS"]) == (
        "2",
        "Yes",
        "4.0",
    )
    assert len(rows) == 6
    # "person_id" is deliberately absent: partition_cohort would prefer it over accession_id.
    assert "person_id" not in prepare.DATAFRAME_COLUMNS


def test_series_uid_matches_the_dicom_writer(prepare: ModuleType) -> None:
    convert = load_script("convert_mha_to_dicom")
    from pydicom.uid import generate_uid

    expected = generate_uid(
        prefix=convert.UID_PREFIX, entropy_srcs=["10000", "1000000", convert.MODALITY_UID_COMPONENT["adc"]]
    )
    assert prepare.series_uid("10000", "1000000", "adc") == expected


def test_site_tree_is_the_planner_layout(prepare: ModuleType, download: Path, tmp_path: Path) -> None:
    site_tree = tmp_path / "sites"
    prepare.prepare(download, download, [f"{p}_{s}" for p, s, _ in CASES], site_tree=site_tree)

    assert sorted(p.name for p in site_tree.iterdir()) == ["PCNN", "RUMC", "ZGT"]
    zgt = site_tree / "ZGT"
    with (zgt / "manifest.csv").open() as handle:
        assert list(csv.DictReader(handle)) == [{"patient_id": "10000", "study_id": "1000000"}]
    for sub, name in (
        ("nifti", "10000_1000000_t2w.nii.gz"),
        ("labels", "10000_1000000.nii.gz"),
        ("zonal_labels", "10000_1000000.nii.gz"),
    ):
        link = zgt / sub / name
        assert link.is_symlink()
        assert link.resolve().is_file()


def test_main_reports_an_empty_download(
    prepare: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "images").mkdir()
    assert prepare.main(["--data-dir", str(tmp_path)]) == 1
    assert "no complete case" in capsys.readouterr().err
