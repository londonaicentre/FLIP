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

"""Case selection: the lowest-N rule for authoring, the published cohort for reproduction."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

DATASETS_DIR = Path(__file__).resolve().parents[3] / "datasets"
sys.path.insert(0, str(DATASETS_DIR))

from brain_mri import cases  # noqa: E402

ALL = ["BRATS_001", "BRATS_002", "BRATS_010", "BRATS_003", "BRATS_100"]


@pytest.fixture
def images_dir(tmp_path: Path) -> Path:
    for case in ALL:
        (tmp_path / f"{case}.nii.gz").write_bytes(b"")
    (tmp_path / "._BRATS_001.nii.gz").write_bytes(b"")  # macOS resource fork
    (tmp_path / "notes.txt").write_text("not a case")
    return tmp_path


def _metadata_table(path: Path, subjects: list[str]) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Subject", "SeriesNumber"])
        writer.writeheader()
        for subject in subjects:
            for series in (1, 2):  # one row per series, as the real table has
                writer.writerow({"Subject": subject, "SeriesNumber": series})
    return path


def test_list_cases_is_natural_order_without_forks(images_dir):
    assert cases.list_cases(images_dir) == ["BRATS_001", "BRATS_002", "BRATS_003", "BRATS_010", "BRATS_100"]


def test_num_cases_keeps_the_lowest_numbered(images_dir):
    """The FLIP#1060 rule spleen uses: N means the N lowest case numbers, so a bigger N is a superset."""
    assert cases.select_cases(images_dir, num_cases=2) == ["BRATS_001", "BRATS_002"]
    assert cases.select_cases(images_dir, num_cases=4) == ["BRATS_001", "BRATS_002", "BRATS_003", "BRATS_010"]


def test_no_selector_means_every_case(images_dir):
    assert cases.select_cases(images_dir) == cases.list_cases(images_dir)


@pytest.mark.parametrize("bad", [0, -1, 6])
def test_num_cases_outside_the_available_range_is_refused(images_dir, bad):
    with pytest.raises(ValueError, match="num_cases"):
        cases.select_cases(images_dir, num_cases=bad)


def test_metadata_table_selects_exactly_the_published_cohort(images_dir, tmp_path):
    table = _metadata_table(tmp_path / "dicom_metadata.csv", ["BRATS_010", "BRATS_002"])
    assert cases.select_cases(images_dir, metadata_table=table) == ["BRATS_002", "BRATS_010"]


def test_metadata_table_naming_an_absent_case_is_loud(images_dir, tmp_path):
    table = _metadata_table(tmp_path / "dicom_metadata.csv", ["BRATS_002", "BRATS_999"])
    with pytest.raises(SystemExit, match="BRATS_999"):
        cases.select_cases(images_dir, metadata_table=table)


def test_both_selectors_together_is_an_error(images_dir, tmp_path):
    table = _metadata_table(tmp_path / "dicom_metadata.csv", ["BRATS_002"])
    with pytest.raises(ValueError, match="one of"):
        cases.select_cases(images_dir, num_cases=1, metadata_table=table)


def test_an_empty_images_dir_is_loud(tmp_path):
    with pytest.raises(SystemExit, match="no .nii.gz"):
        cases.select_cases(tmp_path)
