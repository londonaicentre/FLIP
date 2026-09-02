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

"""The prostate OMOP conversion: one image row per series, the marksheet as clinical rows, the vendor split."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

DATASETS_DIR = Path(__file__).resolve().parents[3] / "datasets"
SCRIPT_PATH = DATASETS_DIR / "prostate" / "omop_convert_prostate.py"

METADATA_COLUMNS = [
    "patient_id", "study_id", "modality", "PatientID", "PatientSex", "PatientAge", "StudyDate",
    "StudyInstanceUID", "SeriesInstanceUID", "SeriesDescription", "AccessionNumber", "Modality",
    "Manufacturer", "ManufacturerModelName", "SliceThickness", "Rows", "Columns", "PixelSpacing",
    "ClinicalTrialSiteID", "NumberOfInstances", "source_trust",
]  # fmt: skip
MARKSHEET_COLUMNS = [
    "patient_id", "study_id", "mri_date", "patient_age", "psa", "psad", "prostate_volume", "histopath_type",
    "lesion_PIRADS", "lesion_GS", "lesion_ISUP", "case_ISUP", "case_csPCa", "center",
]  # fmt: skip

# Three studies over two patients: 10000 has two Siemens studies (trust 1), 10540 one Philips (trust 2).
STUDIES = [
    # patient, study, date,       age, psa,  psad,  vol,  pirads, isup, cspca, vendor
    ("10000", "1000000", "20190702", "73", "7.7", "", "55", "N/A", "0", "NO", "SIEMENS"),
    ("10000", "1000001", "20200811", "74", "9.1", "0.15", "60", "5,2", "2", "YES", "SIEMENS"),
    ("10540", "1000550", "20180301", "65", "", "", "", "3", "0", "NO", "Philips Medical Systems"),
]
MODALITIES = ("t2w", "adc", "hbv")


@pytest.fixture(scope="module")
def converter() -> ModuleType:
    """Import the converter with datasets/ on sys.path so `utils` resolves."""
    sys.path.insert(0, str(DATASETS_DIR))
    spec = importlib.util.spec_from_file_location("fl_tutorials_under_test.omop_convert_prostate", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_sources(root: Path) -> tuple[Path, Path]:
    metadata = root / "dicom_metadata.csv"
    with metadata.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=METADATA_COLUMNS)
        writer.writeheader()
        for i, (patient, study, date, age, *_rest, vendor) in enumerate(STUDIES):
            for j, modality in enumerate(MODALITIES):
                writer.writerow(
                    {
                        "patient_id": patient, "study_id": study, "modality": modality, "PatientID": patient,
                        "PatientSex": "M", "PatientAge": f"{age.zfill(3)}Y", "StudyDate": date,
                        "StudyInstanceUID": f"1.2.{i}", "SeriesInstanceUID": f"1.2.{i}.{j}",
                        "SeriesDescription": modality, "AccessionNumber": f"{patient}_{study}", "Modality": "MR",
                        "Manufacturer": vendor, "ManufacturerModelName": "Skyra", "SliceThickness": "3.0",
                        "Rows": "640", "Columns": "640", "PixelSpacing": "0.5\\0.5", "ClinicalTrialSiteID": "PCNN",
                        "NumberOfInstances": "31", "source_trust": "1" if vendor == "SIEMENS" else "2",
                    }  # fmt: skip
                )
    marksheet = root / "marksheet.csv"
    with marksheet.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MARKSHEET_COLUMNS)
        writer.writeheader()
        for patient, study, date, age, psa, psad, vol, pirads, isup, cspca, _vendor in STUDIES:
            writer.writerow(
                {
                    "patient_id": patient, "study_id": study, "mri_date": f"{date[:4]}-{date[4:6]}-{date[6:]}",
                    "patient_age": age, "psa": psa, "psad": psad, "prostate_volume": vol, "histopath_type": "MRBx",
                    "lesion_PIRADS": pirads, "lesion_GS": "", "lesion_ISUP": "", "case_ISUP": isup,
                    "case_csPCa": cspca, "center": "PCNN",
                }  # fmt: skip
            )
    return metadata, marksheet


@pytest.fixture
def tables(converter, tmp_path) -> dict[str, pd.DataFrame]:
    metadata, marksheet = write_sources(tmp_path)
    return converter.transform_metadata_to_omop_tables(str(metadata), str(marksheet), str(tmp_path))


class TestMaxPirads:
    def test_highest_lesion_wins_and_na_is_no_score(self, converter):
        assert converter.max_pirads("5,2") == 5.0
        assert converter.max_pirads("3") == 3.0
        assert pd.isna(converter.max_pirads("N/A"))
        assert pd.isna(converter.max_pirads(""))
        assert pd.isna(converter.max_pirads(float("nan")))


class TestTables:
    def test_one_person_per_patient_with_year_of_birth_from_age(self, tables):
        person = tables["person"].set_index("person_id")
        assert sorted(person.index) == [10000, 10540]
        assert person.loc[10000, "year_of_birth"] == 2019 - 73  # from the patient's first study
        assert person.loc[10540, "year_of_birth"] == 2018 - 65
        assert set(person["gender_concept_id"]) == {442985}
        assert list(person["trust"]) == ["trust_1", "trust_2"]

    def test_one_visit_and_procedure_per_study(self, tables, converter):
        assert len(tables["visit_occurrence"]) == 3
        assert len(tables["procedure_occurrence"]) == 3
        assert set(tables["procedure_occurrence"]["procedure_concept_id"]) == {converter.PROCEDURE_CONCEPT_ID}
        assert set(tables["procedure_occurrence"]["visit_occurrence_id"]) == set(
            tables["visit_occurrence"]["visit_occurrence_id"]
        )

    def test_one_image_occurrence_per_series_keyed_by_accession(self, tables, converter):
        image = tables["image_occurrence"]
        assert len(image) == 9
        assert sorted(image["accession_id"].unique()) == ["10000_1000000", "10000_1000001", "10540_1000550"]
        assert image["image_series_uid"].is_unique
        assert set(image["modality_concept_id"]) == {4013636}
        assert set(image["anatomic_site_concept_id"]) == {converter.ANATOMIC_SITE_CONCEPT_ID}
        # All three series of a study share its visit and procedure.
        per_study = image.groupby("accession_id")[["visit_occurrence_id", "procedure_occurrence_id"]].nunique()
        assert (per_study == 1).all().all()

    def test_dicom_attributes_become_image_features_with_matching_measurements(self, tables):
        feature = tables["image_feature"]
        measurement = tables["measurement"]
        assert len(feature) == 9 * 5
        assert set(feature["image_feature_event_id"]) <= set(measurement["measurement_id"])
        thickness = measurement[measurement["measurement_source_value"] == "00180050"]
        assert set(thickness["unit_concept_id"]) == {8588}
        assert set(thickness["value_as_number"]) == {3.0}

    def test_marksheet_measurements_skip_blanks_and_carry_units(self, tables, converter):
        measurement = tables["measurement"]
        clinical = measurement[measurement["measurement_source_value"].isin(converter.CLINICAL_MEASUREMENTS)]
        by_kind = clinical.groupby("measurement_source_value")["value_as_number"].apply(list).to_dict()
        assert by_kind == {"prostate_volume": [55.0, 60.0], "psa": [7.7, 9.1], "psad": [0.15]}
        assert set(clinical.loc[clinical["measurement_source_value"] == "psa", "unit_concept_id"]) == {8842}
        assert measurement["measurement_id"].is_unique

    def test_marksheet_observations(self, tables, converter):
        observation = tables["observation"]
        isup = observation[observation["observation_source_value"] == "case_ISUP"].sort_values("person_id")
        assert list(isup["value_as_number"]) == [0.0, 2.0, 0.0]
        assert list(isup["value_as_concept_id"]) == [0, converter.ISUP_VALUE_CONCEPTS[2], 0]
        cspca = observation[observation["observation_source_value"] == "case_csPCa"]
        assert list(cspca["value_as_concept_id"]) == [4188540, 4188539, 4188540]
        pirads = observation[observation["observation_source_value"] == "lesion_PIRADS"]
        assert list(pirads["value_as_number"]) == [5.0, 3.0], "N/A yields no row"
        assert observation["observation_id"].is_unique

    def test_surrogate_ids_live_in_the_prostate_block(self, tables):
        for table, column in (
            ("visit_occurrence", "visit_occurrence_id"),
            ("image_occurrence", "image_occurrence_id"),
            ("measurement", "measurement_id"),
            ("observation", "observation_id"),
        ):
            ids = tables[table][column]
            assert ids.between(3_000_001, 3_999_999).all(), table
            assert ids.is_unique, table

    def test_written_tables_are_read_back_by_the_split(self, converter, tmp_path, tables):
        split = converter.split_data_into_trusts(tables, str(tmp_path))
        assert sorted(split) == ["trust_1", "trust_2"]
        assert len(split["trust_1"]["image_occurrence"]) == 6
        assert len(split["trust_2"]["image_occurrence"]) == 3
        assert list(split["trust_2"]["person"]["person_id"]) == [10540]
        written = pd.read_csv(tmp_path / "omop" / "trust_2" / "prostate_project" / "observation.csv")
        assert "trust" not in written.columns
        assert set(written["person_id"]) == {10540}
