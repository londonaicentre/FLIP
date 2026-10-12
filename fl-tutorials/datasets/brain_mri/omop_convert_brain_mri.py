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

"""``brain_mri_project``: the per-series metadata table → OMOP CDM tables.

On the shared contract spleen and cxr use (``utils/``: schemas, concept mappings, the per-project
surrogate-key block) and the same output layout their verification gate reads: ``omop/<project>/*.csv``
(every row, with a ``trust`` column) and ``omop/<trust>/<project>/*.csv`` (the per-trust split, what
``omop_db_tools.dataset build`` assembles into the canonical dataset with ``source_trust``).

The input has one row per **series** where spleen's has one per subject, so each table is built at
its own level: ``person`` per case, ``visit_occurrence`` and ``procedure_occurrence`` per study,
``image_occurrence`` per series (four rows share an ``accession_id``, as prostate publishes), and the
DICOM-attribute ``image_feature`` + ``measurement`` rows per series, the five attributes spleen
publishes. The surrogate key is the first column of every table — the gate sorts both sides by it.

``person_id`` is the first nine digits of the synthetic NHS number (``PatientID``), the spleen/cxr
convention; the other keys come from ``brain_mri_project``'s reserved block.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd
from pydicom.datadict import tag_for_keyword
from utils.omop_ids import surrogate_ids
from utils.omop_mappings import (
    DICOM_ATTRIBUTE_CONCEPT_CLASS_ID,
    EHR_TYPE_CONCEPT_ID,
    IMAGE_FEATURE_EVENT_FIELD_CONCEPT_ID,
    INPATIENT_VISIT_CONCEPT_ID,
    MAPPING_ANATOMIC_SITE,
    MAPPING_DICOM,
    MAPPING_MODALITY,
    MAPPING_PROCEDURE_TYPE,
    MAPPING_SEX,
    MILLIMETER_UNIT_CONCEPT_ID,
    UNKNOWN_CONCEPT_ID,
)
from utils.omop_schemas import schemas
from utils.synthetic_identity import person_id

PROJECT = "brain_mri_project"
# Per-dataset facts, not OMOP conventions (those live in utils.omop_mappings).
ANATOMIC_SITE_CONCEPT_ID = MAPPING_ANATOMIC_SITE["brain"]  # Brain structure (SNOMED 12738006)
# DICOM-attribute features, as spleen publishes them, keyed by tag so the concept and the unit filter
# can never disagree (FLIP#1098).
DICOM_ATTRIBUTE_KEYWORDS = ["Manufacturer", "ManufacturerModelName", "SliceThickness", "Rows", "Columns"]
SLICE_THICKNESS_TAG = f"{tag_for_keyword('SliceThickness'):08x}"
DEFAULT_METADATA = Path("data/brain_mri/source/dicom_metadata.csv")
STUDY_LEVEL_COLUMNS = ["AccessionNumber", "PatientID", "StudyDate", "StudyTime", "StudyDescription", "trust"]


def read_metadata(csv_path: Path) -> pd.DataFrame:
    """Every column as text (leading zeros survive), plus the ``trust`` directory name each row belongs to."""
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    df["trust"] = "trust_" + df["source_trust"].astype(int).astype(str)
    df["StudyTime"] = [f"{int(t):06d}" for t in df["StudyTime"]]
    return df


def transform_metadata_to_omop_tables(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """The six tables, each validated against its schema and then given a ``trust`` column."""
    tables: dict[str, pd.DataFrame] = {}
    studies = df.drop_duplicates("AccessionNumber")[STUDY_LEVEL_COLUMNS + ["PatientSex", "PatientBirthDate"]]
    studies = studies.reset_index(drop=True)
    patients = studies.drop_duplicates("PatientID").reset_index(drop=True)
    study_datetime = pd.to_datetime(studies["StudyDate"] + studies["StudyTime"], format="%Y%m%d%H%M%S")

    person = pd.DataFrame()
    person["person_id"] = patients["PatientID"].map(person_id)
    person["gender_concept_id"] = patients["PatientSex"].map(MAPPING_SEX)
    person["birth_datetime"] = pd.to_datetime(patients["PatientBirthDate"], format="%Y%m%d")
    person["year_of_birth"] = person["birth_datetime"].dt.year.astype("int64")
    person["month_of_birth"] = person["birth_datetime"].dt.month.astype("int64")
    person["day_of_birth"] = person["birth_datetime"].dt.day.astype("int64")
    person["race_concept_id"] = UNKNOWN_CONCEPT_ID
    person["ethnicity_concept_id"] = UNKNOWN_CONCEPT_ID
    person["gender_source_value"] = patients["PatientSex"]
    person["person_source_value"] = patients["PatientID"]
    person = schemas["person"].validate(person)
    person["trust"] = patients["trust"]
    tables["person"] = person

    visit = pd.DataFrame()
    visit["visit_occurrence_id"] = surrogate_ids(PROJECT, len(studies))
    visit["person_id"] = studies["PatientID"].map(person_id)
    visit["visit_concept_id"] = INPATIENT_VISIT_CONCEPT_ID  # https://athena.ohdsi.org/search-terms/terms/9201
    visit["visit_start_date"] = pd.to_datetime(studies["StudyDate"], format="%Y%m%d")
    visit["visit_start_datetime"] = study_datetime
    visit["visit_end_date"] = visit["visit_start_date"]
    visit["visit_end_datetime"] = visit["visit_start_datetime"]
    visit["visit_type_concept_id"] = EHR_TYPE_CONCEPT_ID
    visit = schemas["visit_occurrence"].validate(visit)
    visit["trust"] = studies["trust"]
    tables["visit_occurrence"] = visit

    procedure = pd.DataFrame()
    procedure["procedure_occurrence_id"] = surrogate_ids(PROJECT, len(studies))
    procedure["person_id"] = visit["person_id"]
    procedure["procedure_concept_id"] = (
        studies["StudyDescription"].str.lower().map(MAPPING_PROCEDURE_TYPE).astype("int64")
    )
    procedure["procedure_date"] = visit["visit_start_date"]
    procedure["procedure_datetime"] = study_datetime
    procedure["procedure_type_concept_id"] = EHR_TYPE_CONCEPT_ID  # EHR, https://doi.org/10.1007/s10278-024-00982-6
    procedure["quantity"] = 1
    procedure["visit_occurrence_id"] = visit["visit_occurrence_id"]
    procedure["procedure_source_value"] = studies["StudyDescription"]
    procedure = schemas["procedure_occurrence"].validate(procedure)
    procedure["trust"] = studies["trust"]
    tables["procedure_occurrence"] = procedure

    # Per series: look its study's keys up by accession.
    by_accession = pd.DataFrame(
        {
            "AccessionNumber": studies["AccessionNumber"],
            "visit_occurrence_id": visit["visit_occurrence_id"],
            "procedure_occurrence_id": procedure["procedure_occurrence_id"],
        }
    )
    series = df.merge(by_accession, on="AccessionNumber", how="left", validate="many_to_one")

    image = pd.DataFrame()
    image["image_occurrence_id"] = surrogate_ids(PROJECT, len(series))
    image["person_id"] = series["PatientID"].map(person_id)
    image["procedure_occurrence_id"] = series["procedure_occurrence_id"]
    image["visit_occurrence_id"] = series["visit_occurrence_id"]
    image["anatomic_site_concept_id"] = ANATOMIC_SITE_CONCEPT_ID
    image["local_path"] = "./dicom/" + series["AccessionNumber"]
    image["image_occurrence_date"] = pd.to_datetime(series["StudyDate"], format="%Y%m%d")
    image["image_study_uid"] = series["StudyInstanceUID"]
    image["image_series_uid"] = series["SeriesInstanceUID"]
    image["modality_concept_id"] = series["Modality"].map(MAPPING_MODALITY).astype("int64")
    image["accession_id"] = series["AccessionNumber"]
    image = schemas["image_occurrence"].validate(image)
    image["trust"] = series["trust"]
    tables["image_occurrence"] = image

    # DICOM attributes → long format, one image_feature + one measurement per (series, attribute).
    tags = [f"{tag_for_keyword(keyword):08x}" for keyword in DICOM_ATTRIBUTE_KEYWORDS]
    features = pd.concat(
        [
            image[["image_occurrence_id", "person_id", "visit_occurrence_id", "image_occurrence_date", "trust"]],
            series[DICOM_ATTRIBUTE_KEYWORDS].rename(columns=dict(zip(DICOM_ATTRIBUTE_KEYWORDS, tags))),
        ],
        axis=1,
    ).melt(
        id_vars=["image_occurrence_id", "person_id", "visit_occurrence_id", "image_occurrence_date", "trust"],
        var_name="measurement_source_value",
        value_name="value_source_value",
    )
    features["measurement_concept_id"] = features["measurement_source_value"].map(MAPPING_DICOM).astype("int64")
    features["id"] = surrogate_ids(PROJECT, len(features))
    is_thickness = features["measurement_source_value"].eq(SLICE_THICKNESS_TAG)

    image_feature = pd.DataFrame()
    image_feature["image_feature_id"] = features["id"]
    image_feature["person_id"] = features["person_id"]
    image_feature["image_occurrence_id"] = features["image_occurrence_id"]
    image_feature["image_feature_event_field_concept_id"] = IMAGE_FEATURE_EVENT_FIELD_CONCEPT_ID
    image_feature["image_feature_event_id"] = features["id"]  # the measurement row it points at
    image_feature["image_feature_concept_id"] = features["measurement_concept_id"]
    image_feature["image_feature_type_concept_id"] = DICOM_ATTRIBUTE_CONCEPT_CLASS_ID
    image_feature["anatomic_site_concept_id"] = ANATOMIC_SITE_CONCEPT_ID
    image_feature = schemas["image_feature"].validate(image_feature)
    image_feature["trust"] = features["trust"]
    tables["image_feature"] = image_feature

    measurement = pd.DataFrame()
    measurement["measurement_id"] = features["id"]
    measurement["person_id"] = features["person_id"]
    measurement["measurement_concept_id"] = features["measurement_concept_id"]
    measurement["measurement_date"] = features["image_occurrence_date"]
    measurement["measurement_type_concept_id"] = EHR_TYPE_CONCEPT_ID
    measurement["value_as_number"] = pd.to_numeric(features["value_source_value"], errors="coerce")
    measurement["unit_concept_id"] = UNKNOWN_CONCEPT_ID
    measurement.loc[is_thickness, "unit_concept_id"] = MILLIMETER_UNIT_CONCEPT_ID
    measurement["visit_occurrence_id"] = features["visit_occurrence_id"]
    measurement["measurement_source_value"] = features["measurement_source_value"]
    measurement.loc[is_thickness, "unit_source_value"] = "millimeter"
    measurement["value_source_value"] = features["value_source_value"].astype(str)
    measurement = schemas["measurement"].validate(measurement)
    measurement["trust"] = features["trust"]
    tables["measurement"] = measurement

    for name in tables:
        print(f"Created and validated table: {name}")
    return tables


def write_tables(tables: dict[str, pd.DataFrame], omop_root: Path) -> None:
    """``<omop_root>/omop/<project>/*.csv`` with ``trust``, and ``<omop_root>/omop/<trust>/<project>/*.csv`` without."""
    combined_dir = Path(omop_root) / "omop" / PROJECT
    combined_dir.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.to_csv(combined_dir / f"{name}.csv", index=False)
    trusts = sorted({trust for table in tables.values() for trust in table["trust"].unique()})
    for trust in trusts:
        trust_dir = Path(omop_root) / "omop" / trust / PROJECT
        shutil.rmtree(trust_dir, ignore_errors=True)
        trust_dir.mkdir(parents=True)
        for name, table in tables.items():
            part = table.loc[table["trust"].eq(trust)].drop(columns="trust")
            schemas[name].validate(part)
            part.to_csv(trust_dir / f"{name}.csv", index=False)
    print(f"✅ {PROJECT}: {', '.join(tables)} → {combined_dir} and per-trust {trusts}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--omop-root", type=Path, default=Path("."), help="tables land under <omop-root>/omop/")
    args = parser.parse_args(argv)

    write_tables(transform_metadata_to_omop_tables(read_metadata(args.metadata)), args.omop_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
