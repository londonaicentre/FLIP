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

"""The spleen OMOP conversion: person identity, table set, and the per-trust split."""

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

DATASETS_DIR = Path(__file__).resolve().parents[3] / "datasets"
SCRIPT_PATH = DATASETS_DIR / "spleen" / "omop_convert_spleen.py"

# Every column transform_dicom_metadata_to_omop_tables() reads. PatientBirthDate, StudyTime and
# StudyDescription are load-bearing: birth date is parsed with format="%Y%m%d", StudyTime is coerced
# via int(), and StudyDescription is lower-cased and looked up in MAPPING_PROCEDURE_TYPE — so it must
# be one of its keys ("ct spleen" / "spleen ct") or procedure_concept_id becomes NaN and Pandera
# rejects the table.
METADATA_COLUMNS = [
    "Subject", "FileName", "FilePath", "PatientID", "PatientName", "PatientSex",
    "PatientBirthDate", "AccessionNumber", "Modality", "StudyDate", "StudyTime",
    "StudyDescription", "StudyInstanceUID", "SeriesInstanceUID",
    "Manufacturer", "ManufacturerModelName", "SliceThickness", "Rows", "Columns",
]


@pytest.fixture(scope="module")
def converter() -> ModuleType:
    """Import the converter with datasets/ on sys.path so `utils` resolves."""
    sys.path.insert(0, str(DATASETS_DIR))
    module_name = "fl_tutorials_under_test.omop_convert_spleen"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


def _write_metadata_csv(path: Path, count: int) -> Path:
    """Write a metadata CSV with `count` distinct subjects."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=METADATA_COLUMNS)
        writer.writeheader()
        for index in range(count):
            nhs = f"{100000000 + index:09d}"
            writer.writerow(
                {
                    "Subject": f"spleen_{index}", "FileName": "0000.dcm",
                    "FilePath": f"/tmp/dicom_output/spleen_{index}/0000.dcm",
                    "PatientID": f"{nhs[:3]} {nhs[3:6]} {nhs[6:]}", "PatientName": f"spleen_{index}",
                    "PatientSex": "M", "PatientBirthDate": "19500101",
                    "AccessionNumber": nhs, "Modality": "CT",
                    "StudyDate": "20200101", "StudyTime": "120000",
                    "StudyDescription": "Spleen CT", "StudyInstanceUID": f"1.2.3.{index}",
                    "SeriesInstanceUID": f"1.2.3.{index}.1", "Manufacturer": "ACME",
                    "ManufacturerModelName": "Scanner9000", "SliceThickness": "5.0",
                    "Rows": "512", "Columns": "512",
                }
            )
    return path


def test_nhs_number_becomes_a_nine_digit_integer(converter: ModuleType) -> None:
    assert converter.nhs_number_to_integer("123 456 789") == 123456789


def test_transform_produces_the_expected_tables(converter: ModuleType, tmp_path: Path) -> None:
    csv_path = _write_metadata_csv(tmp_path / "dicom_metadata.csv", count=4)

    tables = converter.transform_dicom_metadata_to_omop_tables(str(csv_path))

    for name in ("person", "visit_occurrence", "procedure_occurrence", "image_occurrence"):
        assert name in tables, f"{name} is a canonical table the importer requires"
        assert len(tables[name]) == 4
        assert "trust" in tables[name].columns, "the trust column drives the split"


def test_image_occurrence_carries_the_accession_and_uids(converter: ModuleType, tmp_path: Path) -> None:
    """accession_id is what the cohort query and imaging-api route on."""
    csv_path = _write_metadata_csv(tmp_path / "dicom_metadata.csv", count=2)

    tables = converter.transform_dicom_metadata_to_omop_tables(str(csv_path))
    image_occurrence = tables["image_occurrence"]

    assert list(image_occurrence["image_study_uid"]) == ["1.2.3.0", "1.2.3.1"]
    assert image_occurrence["accession_id"].notna().all()


def test_split_writes_one_directory_per_trust_without_the_trust_column(
    converter: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = _write_metadata_csv(tmp_path / "dicom_metadata.csv", count=4)
    tables = converter.transform_dicom_metadata_to_omop_tables(str(csv_path))
    monkeypatch.chdir(tmp_path)

    converter.split_data_into_trusts_and_copy_dicoms(tables, COPY_DICOM=False)

    for trust in converter.TRUSTS:
        person_csv = tmp_path / "omop" / trust / converter.PROJECT / "person.csv"
        assert person_csv.is_file(), f"{trust} must get its own person.csv"
        header = person_csv.read_text().splitlines()[0]
        assert "trust" not in header.split(","), "the trust column is internal to the split"


def test_split_is_round_robin_over_the_row_index(converter: ModuleType, tmp_path: Path) -> None:
    """Documented as-is: MSD is single-source, so the split carries no site heterogeneity.

    This pins the imported behaviour. FLIP#1092 follow-up 2 replaces it with a real site split.
    """
    csv_path = _write_metadata_csv(tmp_path / "dicom_metadata.csv", count=4)

    tables = converter.transform_dicom_metadata_to_omop_tables(str(csv_path))

    trusts = list(tables["person"]["trust"])
    assert trusts[0] != trusts[1], "adjacent rows alternate trusts"
    assert trusts[0] == trusts[2], "the cycle repeats every len(TRUSTS) rows"
    assert trusts[1] == trusts[3], "the cycle repeats every len(TRUSTS) rows"
