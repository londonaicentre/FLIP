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

"""``arkplus_project``: the Ark+ metadata table → OMOP CDM tables (FLIP#2084).

Same shared contract and output layout as the cxr and prostate converters: ``omop/<project>/*.csv``
(every row, with a ``trust`` column) and ``omop/<trust>/<project>/*.csv`` (the per-trust split that
``omop_db_tools.dataset build`` assembles into the canonical dataset with ``source_trust``).

One accession is one person, visit, procedure and image occurrence, plus one ``image_feature`` →
``observation`` pair per label column holding Yes or No — the shape the three Ark+ cohort queries
pivot. ``procedure_source_value`` separates the splits: ``Chest X-ray`` for training (the fine-tuning
query) and ``Chest X-ray (holdout)`` for the evaluation sets (both evaluation queries). The concepts
are the ones the pre-#2084 loader wrote, so the queries return the same columns on a re-seeded trust.

``person_id`` is the first nine digits of the PatientID (``utils.omop_ids.person_id``, as for cxr);
surrogate keys come from ``arkplus_project``'s reserved 6,000,000 block.
"""

from __future__ import annotations

import argparse
import os
import shutil

import pandas as pd
from utils.omop_ids import person_id, surrogate_ids
from utils.omop_mappings import (
    EHR_TYPE_CONCEPT_ID,
    INPATIENT_VISIT_CONCEPT_ID,
    MAPPING_CXR,
    MAPPING_MODALITY,
    MAPPING_SEX,
    MAPPING_YES_NO,
    UNKNOWN_CONCEPT_ID,
)
from utils.omop_schemas import schemas

PROJECT = "arkplus_project"
PROCEDURE_SOURCE_VALUE = {"train": "Chest X-ray", "holdout": "Chest X-ray (holdout)"}
# Label column → finding concept, as the cohort queries pivot them (SNOMED; Pneumothorax is in the
# Condition domain, having no Observation-domain match).
LABEL_CONCEPTS = {
    "Effusion": 4215818,
    "Edema": 4196943,
    "Consolidation": 4319884,
    "Infiltration": 4264333,
    "Lung Nodule or Mass": 4214641,
    "Pneumothorax": 253796,
    "Lungs in normal arrangement": 40481136,
}


def transform_metadata_to_omop_tables(metadata_csv: str, omop_root: str = ".") -> dict[str, pd.DataFrame]:
    """Build every table; write ``<omop_root>/omop/arkplus_project/*.csv``; return them."""
    df = pd.read_csv(metadata_csv, dtype=str)
    trust = "trust_" + df["source_trust"]
    study_date = pd.to_datetime(df["StudyDate"], format="%Y%m%d")
    study_datetime = pd.to_datetime(df["StudyDate"] + df["StudyTime"].str.zfill(6), format="%Y%m%d%H%M%S")
    combined: dict[str, pd.DataFrame] = {}

    person = pd.DataFrame()
    person["person_id"] = df["PatientID"].map(person_id)
    person["gender_concept_id"] = df["PatientSex"].map(MAPPING_SEX).astype("int64")
    person["year_of_birth"] = df["YearOfBirth"].astype("int64")
    person["race_concept_id"] = UNKNOWN_CONCEPT_ID
    person["ethnicity_concept_id"] = UNKNOWN_CONCEPT_ID
    person["gender_source_value"] = df["PatientSex"]
    person["person_source_value"] = df["PatientID"]
    combined["person"] = schemas["person"].validate(person)

    visit = pd.DataFrame()
    visit["visit_occurrence_id"] = surrogate_ids(PROJECT, len(df))
    visit["person_id"] = person["person_id"]
    visit["visit_concept_id"] = INPATIENT_VISIT_CONCEPT_ID
    visit["visit_start_date"] = study_date
    visit["visit_start_datetime"] = study_datetime
    visit["visit_end_date"] = study_date
    visit["visit_end_datetime"] = study_datetime
    visit["visit_type_concept_id"] = EHR_TYPE_CONCEPT_ID
    combined["visit_occurrence"] = schemas["visit_occurrence"].validate(visit)

    procedure = pd.DataFrame()
    procedure["procedure_occurrence_id"] = surrogate_ids(PROJECT, len(df))
    procedure["person_id"] = person["person_id"]
    procedure["procedure_concept_id"] = MAPPING_CXR["plain_xray"]
    procedure["procedure_date"] = study_date
    procedure["procedure_datetime"] = study_datetime
    procedure["procedure_type_concept_id"] = EHR_TYPE_CONCEPT_ID
    procedure["quantity"] = 1
    procedure["visit_occurrence_id"] = visit["visit_occurrence_id"]
    procedure["procedure_source_value"] = df["split"].map(PROCEDURE_SOURCE_VALUE)
    combined["procedure_occurrence"] = schemas["procedure_occurrence"].validate(procedure)

    image = pd.DataFrame()
    image["image_occurrence_id"] = surrogate_ids(PROJECT, len(df))
    image["person_id"] = person["person_id"]
    image["procedure_occurrence_id"] = procedure["procedure_occurrence_id"]
    image["visit_occurrence_id"] = visit["visit_occurrence_id"]
    image["anatomic_site_concept_id"] = MAPPING_CXR["lung_structure"]
    image["image_occurrence_date"] = study_date
    image["image_study_uid"] = df["StudyInstanceUID"]
    image["image_series_uid"] = df["SeriesInstanceUID"]
    image["modality_concept_id"] = df["Modality"].map(MAPPING_MODALITY).astype("int64")
    image["accession_id"] = df["AccessionNumber"]
    combined["image_occurrence"] = schemas["image_occurrence"].validate(image)

    # One image_feature → observation pair per label; the two share an id, as cxr's do.
    labels = df[list(LABEL_CONCEPTS)].copy()
    labels["image_occurrence_id"] = image["image_occurrence_id"]
    labels = labels.melt(id_vars="image_occurrence_id", var_name="label", value_name="value", ignore_index=False)
    labels = labels.sort_index(kind="stable")  # accession by accession, labels in LABEL_CONCEPTS order
    feature_ids = surrogate_ids(PROJECT, len(labels))
    concepts = labels["label"].map(LABEL_CONCEPTS).values
    positive = labels["value"].eq("Yes").values

    image_feature = pd.DataFrame()
    image_feature["image_feature_id"] = feature_ids
    image_feature["person_id"] = person["person_id"].loc[labels.index].values
    image_feature["image_occurrence_id"] = labels["image_occurrence_id"].values
    image_feature["image_feature_event_field_concept_id"] = MAPPING_CXR["observation"]
    image_feature["image_feature_event_id"] = feature_ids
    image_feature["image_feature_concept_id"] = concepts
    image_feature["image_feature_type_concept_id"] = MAPPING_CXR["ehr_radiology_report"]
    image_feature["anatomic_site_concept_id"] = MAPPING_CXR["lung_structure"]
    combined["image_feature"] = schemas["image_feature"].validate(image_feature)

    observation = pd.DataFrame()
    observation["observation_id"] = feature_ids
    observation["person_id"] = image_feature["person_id"]
    observation["observation_concept_id"] = concepts
    observation["observation_date"] = study_date.loc[labels.index].values
    observation["observation_type_concept_id"] = MAPPING_CXR["ehr_radiology_report"]
    observation["value_as_number"] = positive.astype(float)
    observation["value_as_concept_id"] = [MAPPING_YES_NO["yes"] if p else MAPPING_YES_NO["no"] for p in positive]
    observation["visit_occurrence_id"] = visit["visit_occurrence_id"].loc[labels.index].values
    observation["observation_source_value"] = labels["label"].values
    observation["value_source_value"] = labels["value"].values
    combined["observation"] = schemas["observation"].validate(observation)

    trust_by_table = {
        name: trust.values for name in ("person", "visit_occurrence", "procedure_occurrence", "image_occurrence")
    }
    trust_by_table["image_feature"] = trust_by_table["observation"] = trust.loc[labels.index].values
    out_dir = os.path.join(omop_root, "omop", PROJECT)
    os.makedirs(out_dir, exist_ok=True)
    for name, table in combined.items():
        table["trust"] = trust_by_table[name]
        table.to_csv(os.path.join(out_dir, f"{name}.csv"), index=False)
        print(f"Created and validated table: {name} ({len(table)} rows)")
    return combined


def split_data_into_trusts(
    combined: dict[str, pd.DataFrame], omop_root: str = "."
) -> dict[str, dict[str, pd.DataFrame]]:
    """``omop/<trust>/arkplus_project/<table>.csv`` per trust — the layout the gate and ``dataset build`` read."""
    trusts = sorted({t for table in combined.values() for t in table["trust"].unique()})
    by_trust: dict[str, dict[str, pd.DataFrame]] = {}
    for trust in trusts:
        trust_dir = os.path.join(omop_root, "omop", trust, PROJECT)
        if os.path.isdir(trust_dir):
            shutil.rmtree(trust_dir)
        os.makedirs(trust_dir)
        by_trust[trust] = {}
        for name, table in combined.items():
            part = table.loc[table["trust"].eq(trust)].drop(columns="trust")
            schemas[name].validate(part)
            part.to_csv(os.path.join(trust_dir, f"{name}.csv"), index=False)
            by_trust[trust][name] = part
    return by_trust


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--metadata", default="data/arkplus/source/dicom_metadata.csv")
    parser.add_argument("--omop-root", default=".", help="writes <omop-root>/omop/arkplus_project and omop/<trust>/")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    tables = transform_metadata_to_omop_tables(args.metadata, args.omop_root)
    for trust, part in split_data_into_trusts(tables, args.omop_root).items():
        print(f"{trust}: {len(part['person'])} persons, {len(part['image_occurrence'])} images")
