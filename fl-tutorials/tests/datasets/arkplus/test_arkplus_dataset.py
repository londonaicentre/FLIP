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

"""arkplus_project: the metadata table, the OMOP conversion and the DICOM fills (FLIP#2084)."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pydicom
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

DATASETS_DIR = Path(__file__).resolve().parents[3] / "datasets"
LABELS = (
    "Effusion", "Edema", "Consolidation", "Infiltration", "Lung Nodule or Mass", "Pneumothorax",
    "Lungs in normal arrangement",
)  # fmt: skip
# folder → [(source name, AccessionNumber, positive label)]
TREE = {
    "site1": [("final_2_method1_effusion_left_A", "100000001001", "Effusion"),
              ("final_2_method1_pneumothorax_right_B", "100000002002", "Pneumothorax")],
    "site1_holdoff": [("final_2_method1_infiltration_bilateral_C", "100000003003", "Infiltration")],
    "site2": [("final_1_method2_consolidation_right_D", "200000004004", "Consolidation")],
    "site2_holdoff": [("final_1_method2_lung_nodule_or_mass_left_E", "200000005005", "Lung Nodule or Mass")],
}  # fmt: skip


def load(name: str) -> ModuleType:
    sys.path.insert(0, str(DATASETS_DIR))
    spec = importlib.util.spec_from_file_location(f"arkplus_under_test.{name}", DATASETS_DIR / "arkplus" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder() -> ModuleType:
    return load("create_arkplus_metadata_table")


@pytest.fixture(scope="module")
def converter() -> ModuleType:
    return load("omop_convert_arkplus")


@pytest.fixture(scope="module")
def preparer() -> ModuleType:
    return load("prepare_arkplus_dicom")


def write_dicom(path: Path, accession: str) -> None:
    """A headers-only radiograph shaped like the published ones: no sex, description or referrer."""
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.7"
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.AccessionNumber = accession
    ds.PatientID = accession
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.StudyDate = "20260617"
    ds.StudyTime = "185354"
    ds.Modality = "DX"
    path.parent.mkdir(parents=True)
    pydicom.dcmwrite(path, ds, enforce_file_format=True)


@pytest.fixture
def download(tmp_path: Path) -> Path:
    """The four site folders as the download targets lay them out."""
    for folder, rows in TREE.items():
        site = tmp_path / folder
        for name, accession, _ in rows:
            write_dicom(site / "accession-resources" / name / f"{name}.dcm", accession)
        with (site / "sample_get_dataframe_response.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["accession_id", *LABELS])
            for name, _, positive in rows:
                writer.writerow([name, *("Yes" if label == positive else "No" for label in LABELS)])
    return tmp_path


def test_metadata_table_maps_each_folder_to_its_site_and_split(builder: ModuleType, download: Path) -> None:
    rows = builder.build_table(download)
    got = {(r["folder"], r["source_trust"], r["split"]) for r in rows}
    assert got == {
        ("site1", "1", "train"),
        ("site1_holdoff", "1", "holdout"),
        ("site2", "2", "train"),
        ("site2_holdoff", "2", "holdout"),
    }
    assert [r["AccessionNumber"] for r in rows] == [a for folder in TREE.values() for _, a, _ in folder]


def test_demographics_match_the_legacy_loader(builder: ModuleType) -> None:
    """Re-seeding a trust the pre-#2084 loader filled must not change its cohort's sex or age."""
    digest = hashlib.sha256(b"827497508870").digest()
    expected = ("M" if digest[0] % 2 == 0 else "F", 1940 + digest[1] % 66)
    assert builder.demographics("827497508870") == expected


def test_a_repeated_accession_number_is_refused(builder: ModuleType, download: Path) -> None:
    name = TREE["site2"][0][0]
    dcm = download / "site2" / "accession-resources" / name / f"{name}.dcm"
    dcm.unlink()
    dcm.parent.rmdir()
    write_dicom(dcm, TREE["site1"][0][1])
    with pytest.raises(SystemExit, match="is on both"):
        builder.build_table(download)


def test_a_label_outside_yes_no_is_refused(builder: ModuleType, download: Path) -> None:
    labels = download / "site1" / "sample_get_dataframe_response.csv"
    labels.write_text(labels.read_text().replace(",No,", ",maybe,", 1))
    with pytest.raises(SystemExit, match="label values outside"):
        builder.build_table(download)


@pytest.fixture
def tables(builder: ModuleType, converter: ModuleType, download: Path, tmp_path: Path) -> dict:
    metadata = tmp_path / "source" / "dicom_metadata.csv"
    builder.write_csv(metadata, builder.build_table(download))
    return converter.transform_metadata_to_omop_tables(str(metadata), str(tmp_path / "out"))


def test_the_split_decides_the_procedure_source_value(tables: dict) -> None:
    procedure = tables["procedure_occurrence"].set_index("procedure_occurrence_id")
    image = tables["image_occurrence"]
    value_by_accession = dict(
        zip(image["accession_id"], procedure.loc[image["procedure_occurrence_id"], "procedure_source_value"])
    )
    assert value_by_accession == {
        "100000001001": "Chest X-ray",
        "100000002002": "Chest X-ray",
        "100000003003": "Chest X-ray (holdout)",
        "200000004004": "Chest X-ray",
        "200000005005": "Chest X-ray (holdout)",
    }


def test_every_label_is_one_feature_observation_pair_holding_yes_or_no(converter: ModuleType, tables: dict) -> None:
    feature, observation = tables["image_feature"], tables["observation"]
    assert len(feature) == len(observation) == 5 * len(LABELS)
    assert feature["image_feature_event_id"].tolist() == observation["observation_id"].tolist()
    assert set(feature["image_feature_event_field_concept_id"]) == {1147304}  # the observation table
    positives = observation.loc[observation["value_as_concept_id"].eq(4188539), "observation_source_value"]
    assert sorted(positives) == sorted(positive for rows in TREE.values() for _, _, positive in rows)
    assert set(observation["observation_concept_id"]) == set(converter.LABEL_CONCEPTS.values())


def test_surrogate_keys_stay_in_the_projects_block(tables: dict) -> None:
    for name, key in (
        ("visit_occurrence", "visit_occurrence_id"),
        ("procedure_occurrence", "procedure_occurrence_id"),
        ("image_occurrence", "image_occurrence_id"),
        ("image_feature", "image_feature_id"),
        ("observation", "observation_id"),
    ):
        assert tables[name][key].between(6_000_001, 6_999_999).all(), name


def test_person_source_value_is_the_dicom_patient_id(tables: dict) -> None:
    """publish_dicom.py checks every DICOM PatientID against person_source_value."""
    person = tables["person"]
    assert person["person_source_value"].tolist() == [a for rows in TREE.values() for _, a, _ in rows]
    assert person["person_id"].tolist() == [int(a[:9]) for a in person["person_source_value"]]


def test_each_trust_gets_its_sites_rows(converter: ModuleType, tables: dict, tmp_path: Path) -> None:
    split = converter.split_data_into_trusts(tables, str(tmp_path / "out"))
    assert sorted(split["trust_1"]["image_occurrence"]["accession_id"]) == [
        "100000001001",
        "100000002002",
        "100000003003",
    ]
    assert sorted(split["trust_2"]["image_occurrence"]["accession_id"]) == ["200000004004", "200000005005"]
    assert len(split["trust_1"]["observation"]) == 3 * len(LABELS)


def test_prepare_fills_only_the_empty_required_tags(preparer: ModuleType) -> None:
    ds = Dataset()
    ds.StudyDescription = "Kept"
    assert preparer.fill_required_tags(ds, "F") == ["ReferringPhysicianName", "PatientSex"]
    assert (ds.StudyDescription, str(ds.ReferringPhysicianName), ds.PatientSex) == ("Kept", "Synthetic^Referring", "F")
