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

"""DICOM tree → the per-series metadata table, with source_trust decided here and the quality gates."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from brain_mri import convert_brain_mri_to_dicom as conv
from brain_mri import create_brain_mri_metadata_table as meta
from utils.dicom_writer import SeriesTags, StudyTags, write_series


@pytest.fixture
def dicom_tree(msd_extract, tmp_path) -> Path:
    out = tmp_path / "dicom"
    conv.convert(msd_extract / "imagesTr", msd_extract / "dataset.json", out, ["BRATS_001", "BRATS_002", "BRATS_003"])
    return out


class TestBuildTable:
    def test_one_row_per_series_with_the_fixed_columns_in_order(self, dicom_tree):
        table = meta.build_table(dicom_tree, num_trusts=2, series_per_study=4, min_studies_per_trust=1)

        assert list(table.columns) == meta.COLUMNS
        assert len(table) == 3 * 4
        assert list(table["Subject"].unique()) == ["BRATS_001", "BRATS_002", "BRATS_003"], "natural subject order"
        first = table.iloc[0]
        assert (first["SeriesNumber"], first["SeriesDescription"], first["Modality"]) == ("1", "FLAIR", "MR")
        assert first["NumberOfInstances"] == "3"
        assert first["StudyDescription"] == conv.STUDY_DESCRIPTION
        assert first["StudyTime"].isdigit()
        assert first["PixelSpacing"] == "1.0\\1.0"
        assert float(first["MagneticFieldStrength"]) in {1.5, 3.0}

    def test_source_trust_is_round_robin_over_subjects_and_constant_within_a_study(self, dicom_tree):
        table = meta.build_table(dicom_tree, num_trusts=2, series_per_study=4, min_studies_per_trust=1)
        by_subject = table.groupby("Subject")["source_trust"].unique()
        assert by_subject["BRATS_001"].tolist() == ["1"]
        assert by_subject["BRATS_002"].tolist() == ["2"]
        assert by_subject["BRATS_003"].tolist() == ["1"]

    def test_the_series_of_a_study_stay_in_series_number_order(self, dicom_tree):
        table = meta.build_table(dicom_tree, num_trusts=2, series_per_study=4, min_studies_per_trust=1)
        assert table[table["Subject"] == "BRATS_002"]["SeriesNumber"].tolist() == ["1", "2", "3", "4"]

    def test_writes_the_csv_where_asked(self, dicom_tree, tmp_path):
        out = tmp_path / "source" / "dicom_metadata.csv"
        meta.main(["--dicom-dir", str(dicom_tree), "--output", str(out), "--min-studies-per-trust", "1"])
        assert out.read_text().splitlines()[0] == ",".join(meta.COLUMNS)


def _study(tmp_path: Path, subject: str, accession: str, patient_id: str, series_numbers: tuple[int, ...]) -> None:
    study = StudyTags(
        patient_id=patient_id, patient_name="A^B", patient_sex="F", patient_birth_date="19700101",
        accession_number=accession, study_date="20150101", study_time="120000", study_description="x",
        referring_physician_name="C^D", institution_name="I", extra={"ClinicalTrialSubjectID": subject},
    )  # fmt: skip
    for number in series_numbers:
        series = SeriesTags("MR", number, f"S{number}", f"S{number}", "BRAIN", "M", "Model")
        write_series(
            np.zeros((2, 2, 1)), np.eye(4), tmp_path / accession, study=study, series=series, uid_entropy=[subject]
        )


class TestQualityChecks:
    def test_a_study_with_too_few_series_is_refused(self, tmp_path):
        _study(tmp_path, "BRATS_001", "FAK1", "111 111 1111", (1, 2, 3))
        with pytest.raises(SystemExit, match="series"):
            meta.build_table(tmp_path, num_trusts=1, series_per_study=4, min_studies_per_trust=1)

    def test_two_subjects_sharing_an_accession_is_refused(self, tmp_path):
        _study(tmp_path, "BRATS_001", "FAK1", "111 111 1111", (1,))
        _study(tmp_path, "BRATS_002", "FAK1", "222 222 2222", (1,))
        with pytest.raises(SystemExit, match="accession"):
            meta.build_table(tmp_path, num_trusts=1, series_per_study=1, min_studies_per_trust=1)

    def test_two_subjects_sharing_a_patient_id_is_refused(self, tmp_path):
        _study(tmp_path, "BRATS_001", "FAK1", "111 111 1111", (1,))
        _study(tmp_path, "BRATS_002", "FAK2", "111 111 1111", (1,))
        with pytest.raises(SystemExit, match="PatientID"):
            meta.build_table(tmp_path, num_trusts=1, series_per_study=1, min_studies_per_trust=1)

    def test_a_trust_below_the_cohort_floor_is_refused(self, tmp_path):
        _study(tmp_path, "BRATS_001", "FAK1", "111 111 1111", (1,))
        with pytest.raises(SystemExit, match="COHORT_QUERY_THRESHOLD"):
            meta.build_table(tmp_path, num_trusts=1, series_per_study=1, min_studies_per_trust=10)

    def test_an_empty_tree_is_refused(self, tmp_path):
        with pytest.raises(SystemExit, match="no DICOM"):
            meta.build_table(tmp_path, num_trusts=1, series_per_study=1, min_studies_per_trust=1)
