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

"""The brain_mri_project OMOP conversion: a per-series input, deduplicated to the right level per table."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from brain_mri import create_brain_mri_metadata_table as meta
from brain_mri import omop_convert_brain_mri as omop
from utils.omop_ids import PROJECT_ID_BLOCKS

BLOCK = PROJECT_ID_BLOCKS["brain_mri_project"]


def _row(subject: str, series: int, source_trust: int, patient_id: str, accession: str) -> dict[str, str]:
    return {
        "Subject": subject,
        "PatientID": patient_id,
        "PatientName": "DOE^JANE",
        "PatientSex": "F",
        "PatientBirthDate": "19700315",
        "AccessionNumber": accession,
        "StudyDate": "20150301",
        "StudyTime": "101500",
        "StudyDescription": "MR Brain WO and W contrast IV",
        "StudyInstanceUID": f"1.2.{subject[-3:]}",
        "SeriesInstanceUID": f"1.2.{subject[-3:]}.{series}",
        "SeriesNumber": str(series),
        "SeriesDescription": ["FLAIR", "T1w", "T1Gd", "T2w"][series - 1],
        "Modality": "MR",
        "Manufacturer": "Siemens Healthineers",
        "ManufacturerModelName": "MAGNETOM Skyra",
        "MagneticFieldStrength": "3.0",
        "SliceThickness": "1.0",
        "Rows": "240",
        "Columns": "240",
        "PixelSpacing": "1.0\\1.0",
        "NumberOfInstances": "155",
        "source_trust": str(source_trust),
    }


@pytest.fixture
def metadata_csv(tmp_path: Path) -> Path:
    path = tmp_path / "dicom_metadata.csv"
    rows = [_row("BRATS_001", s, 1, "111 111 1111", "FAK00000001") for s in range(1, 5)]
    rows += [_row("BRATS_002", s, 2, "222 222 2222", "FAK00000002") for s in range(1, 5)]
    rows += [_row("BRATS_003", s, 1, "333 333 3333", "FAK00000003") for s in range(1, 5)]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=meta.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


class TestTransform:
    def test_tables_are_deduplicated_to_the_right_level(self, metadata_csv):
        tables = omop.transform_metadata_to_omop_tables(omop.read_metadata(metadata_csv))

        assert set(tables) == {
            "person",
            "visit_occurrence",
            "procedure_occurrence",
            "image_occurrence",
            "image_feature",
            "measurement",
        }
        assert len(tables["person"]) == 3, "one per case"
        assert len(tables["visit_occurrence"]) == 3, "one per study"
        assert len(tables["procedure_occurrence"]) == 3
        assert len(tables["image_occurrence"]) == 12, "one per series"
        assert len(tables["image_feature"]) == 12 * 5, "five DICOM attributes per series"
        assert len(tables["measurement"]) == 12 * 5

    def test_the_surrogate_id_is_the_first_column_of_every_table(self, metadata_csv):
        """The verify gate sorts both sides by the first column, so it must be the unique key."""
        tables = omop.transform_metadata_to_omop_tables(omop.read_metadata(metadata_csv))
        expected_first = {
            "person": "person_id",
            "visit_occurrence": "visit_occurrence_id",
            "procedure_occurrence": "procedure_occurrence_id",
            "image_occurrence": "image_occurrence_id",
            "image_feature": "image_feature_id",
            "measurement": "measurement_id",
        }
        for name, table in tables.items():
            assert table.columns[0] == expected_first[name], name
            assert table[table.columns[0]].is_unique, name

    def test_ids_come_from_the_brain_mri_block(self, metadata_csv):
        tables = omop.transform_metadata_to_omop_tables(omop.read_metadata(metadata_csv))
        for name in ("visit_occurrence", "procedure_occurrence", "image_occurrence", "image_feature", "measurement"):
            ids = tables[name][tables[name].columns[0]]
            assert ids.min() == BLOCK + 1, name
            assert ids.max() <= BLOCK + 1_000_000, name
        assert tables["person"]["person_id"].tolist() == [111111111, 222222222, 333333333]

    def test_image_occurrence_rows_point_at_their_study_and_carry_the_brain_concepts(self, metadata_csv):
        tables = omop.transform_metadata_to_omop_tables(omop.read_metadata(metadata_csv))
        image = tables["image_occurrence"]
        procedure = tables["procedure_occurrence"]

        assert image["accession_id"].value_counts().to_dict() == {"FAK00000001": 4, "FAK00000002": 4, "FAK00000003": 4}
        assert image["image_series_uid"].is_unique
        assert set(image["modality_concept_id"]) == {4013636}
        assert set(image["anatomic_site_concept_id"]) == {4133034}
        assert set(procedure["procedure_concept_id"]) == {3037128}
        assert set(image["local_path"]) == {"./dicom/FAK00000001", "./dicom/FAK00000002", "./dicom/FAK00000003"}
        # Every series of a study shares that study's procedure and visit.
        joined = image.merge(procedure, on="procedure_occurrence_id", suffixes=("", "_p"))
        assert (joined["person_id"] == joined["person_id_p"]).all()
        assert (joined["visit_occurrence_id"] == joined["visit_occurrence_id_p"]).all()

    def test_slice_thickness_measurements_carry_the_millimetre_unit(self, metadata_csv):
        tables = omop.transform_metadata_to_omop_tables(omop.read_metadata(metadata_csv))
        measurement = tables["measurement"]
        thickness = measurement[measurement["measurement_source_value"] == "00180050"]
        assert len(thickness) == 12
        assert set(thickness["unit_concept_id"]) == {8588}
        assert set(thickness["value_as_number"]) == {1.0}
        others = measurement[measurement["measurement_source_value"] != "00180050"]
        assert set(others["unit_concept_id"]) == {0}

    def test_image_features_link_to_their_measurement_and_occurrence(self, metadata_csv):
        tables = omop.transform_metadata_to_omop_tables(omop.read_metadata(metadata_csv))
        feature, measurement = tables["image_feature"], tables["measurement"]
        assert feature["image_feature_id"].tolist() == measurement["measurement_id"].tolist()
        assert feature["image_feature_event_id"].tolist() == measurement["measurement_id"].tolist()
        assert set(feature["image_occurrence_id"]) == set(tables["image_occurrence"]["image_occurrence_id"])

    def test_trust_column_follows_source_trust(self, metadata_csv):
        tables = omop.transform_metadata_to_omop_tables(omop.read_metadata(metadata_csv))
        assert tables["person"]["trust"].tolist() == ["trust_1", "trust_2", "trust_1"]
        assert tables["image_occurrence"]["trust"].value_counts().to_dict() == {"trust_1": 8, "trust_2": 4}


class TestWrite:
    def test_writes_the_combined_and_per_trust_layout_the_gate_reads(self, metadata_csv, tmp_path):
        tables = omop.transform_metadata_to_omop_tables(omop.read_metadata(metadata_csv))
        omop.write_tables(tables, tmp_path)

        combined = tmp_path / "omop" / "brain_mri_project"
        assert sorted(p.name for p in combined.glob("*.csv")) == sorted(f"{t}.csv" for t in tables)
        with (combined / "person.csv").open() as handle:
            assert "trust" in next(csv.reader(handle))
        for trust, persons in (("trust_1", 2), ("trust_2", 1)):
            split = tmp_path / "omop" / trust / "brain_mri_project"
            with (split / "person.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            assert len(rows) == persons
            assert "trust" not in rows[0]

    def test_main_runs_end_to_end(self, metadata_csv, tmp_path):
        omop.main(["--metadata", str(metadata_csv), "--omop-root", str(tmp_path)])
        assert (tmp_path / "omop" / "trust_2" / "brain_mri_project" / "image_feature.csv").is_file()
