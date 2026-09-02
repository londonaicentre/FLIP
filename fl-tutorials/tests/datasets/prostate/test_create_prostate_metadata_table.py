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

"""The prostate metadata table: one row per series, and source_trust decided by scanner vendor."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pydicom
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

DATASETS_DIR = Path(__file__).resolve().parents[3] / "datasets"
SCRIPT_PATH = DATASETS_DIR / "prostate" / "create_prostate_metadata_table.py"


@pytest.fixture(scope="module")
def builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("create_prostate_metadata_table_under_test", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_series(root: Path, patient: str, study: str, modality: str, manufacturer: str, instances: int = 2) -> Path:
    """A tiny DICOM series in the converter's <patient>/<study>/<modality>/ layout, headers only."""
    series_dir = root / patient / study / modality
    series_dir.mkdir(parents=True)
    study_uid, series_uid = generate_uid(), generate_uid()
    for i in range(instances):
        ds = Dataset()
        ds.file_meta = FileMetaDataset()
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
        ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
        ds.PatientID = patient
        ds.PatientSex = "M"
        ds.PatientAge = "073Y"
        ds.StudyDate = "20190702"
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.SeriesDescription = {"t2w": "T2 Weighted", "adc": "ADC Map", "hbv": "High B-Value DWI"}[modality]
        ds.AccessionNumber = f"{patient}_{study}"
        ds.Modality = "MR"
        ds.Manufacturer = manufacturer
        ds.ManufacturerModelName = "Skyra"
        ds.SliceThickness = "3.0"
        ds.Rows = 4
        ds.Columns = 4
        ds.PixelSpacing = ["0.5", "0.5"]
        ds.ClinicalTrialSiteID = "PCNN"
        ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
        ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID
        ds.InstanceNumber = i
        pydicom.dcmwrite(series_dir / f"{i:04d}.dcm", ds, enforce_file_format=True)
    return series_dir


def write_marksheet(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["patient_id", "study_id", "mri_date", "patient_age", "psa", "center"])
        for patient, study, center in rows:
            writer.writerow([patient, study, "2019-07-02", "73", "7.7", center])
    return path


class TestSourceTrust:
    def test_siemens_is_trust_1_and_philips_trust_2(self, builder):
        assert builder.source_trust_for("SIEMENS") == 1
        assert builder.source_trust_for("Philips Medical Systems") == 2

    def test_an_unknown_vendor_is_an_error_not_a_third_trust(self, builder):
        with pytest.raises(SystemExit, match="no trust"):
            builder.source_trust_for("GE MEDICAL SYSTEMS")


class TestBuildTable:
    def test_one_row_per_series_with_the_header_and_the_trust(self, builder, tmp_path):
        write_series(tmp_path, "10000", "1000000", "t2w", "SIEMENS", instances=3)
        write_series(tmp_path, "10000", "1000000", "adc", "SIEMENS")
        write_series(tmp_path, "10540", "1000550", "t2w", "Philips Medical Systems")

        rows = builder.build_table(tmp_path)

        assert [(r["patient_id"], r["study_id"], r["modality"]) for r in rows] == [
            ("10000", "1000000", "adc"),
            ("10000", "1000000", "t2w"),
            ("10540", "1000550", "t2w"),
        ]
        t2w = rows[1]
        assert t2w["AccessionNumber"] == "10000_1000000"
        assert t2w["Manufacturer"] == "SIEMENS"
        assert t2w["PixelSpacing"] == "0.5\\0.5"
        assert t2w["ClinicalTrialSiteID"] == "PCNN"
        assert t2w["NumberOfInstances"] == "3"
        assert [r["source_trust"] for r in rows] == ["1", "1", "2"]
        assert list(rows[0]) == list(builder.COLUMNS)

    def test_an_empty_tree_is_refused(self, builder, tmp_path):
        with pytest.raises(SystemExit, match="no <patient>/<study>/<modality>/"):
            builder.build_table(tmp_path)


class TestTrimmedMarksheet:
    def test_keeps_exactly_the_studies_in_the_table(self, builder, tmp_path):
        marksheet = write_marksheet(
            tmp_path / "m.csv",
            [("10000", "1000000", "PCNN"), ("10001", "1000001", "RUMC"), ("10540", "1000550", "PCNN")],
        )
        rows = [{"patient_id": "10540", "study_id": "1000550"}, {"patient_id": "10000", "study_id": "1000000"}]

        kept = builder.trimmed_marksheet(marksheet, rows)

        assert [(r["patient_id"], r["study_id"]) for r in kept] == [("10000", "1000000"), ("10540", "1000550")]

    def test_a_study_missing_from_the_marksheet_is_an_error(self, builder, tmp_path):
        marksheet = write_marksheet(tmp_path / "m.csv", [("10000", "1000000", "PCNN")])
        with pytest.raises(SystemExit, match="not in"):
            builder.trimmed_marksheet(marksheet, [{"patient_id": "10999", "study_id": "1000999"}])
