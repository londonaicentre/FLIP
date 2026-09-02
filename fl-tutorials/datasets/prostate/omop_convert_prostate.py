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

"""``prostate_project``: the PI-CAI metadata table + marksheet → OMOP CDM tables (FLIP#1100 follow-on).

The third dataset converter under ``fl-tutorials/datasets/``, on the same shared contract as spleen
and cxr (``utils/``: schemas, concept mappings, the per-project surrogate-key block) and the same
output layout their verification gate reads: ``omop/<project>/*.csv`` (every row, with a ``trust``
column) and ``omop/<trust>/<project>/*.csv`` (the per-trust split, what ``omop_db_tools.dataset build``
assembles into the canonical dataset with ``source_trust``).

Inputs are the two files ``create_prostate_metadata_table.py`` writes to ``source/``, published with
the tables so the conversion reproduces from the published inputs alone:

* ``dicom_metadata.csv`` — one row per DICOM series, carrying ``source_trust`` (decided there, from
  the scanner vendor) — becomes ``image_occurrence`` (one row per series) and the DICOM-attribute
  ``image_feature`` + ``measurement`` rows, as for spleen.
* ``marksheet.csv`` — PI-CAI's per-study clinical sheet — becomes what a cohort query can select on:
  PSA, PSA density and prostate volume as ``measurement`` rows; ISUP grade group, clinically
  significant cancer (yes/no) and the highest lesion PI-RADS as ``observation`` rows. The
  segmentation labels are NOT in OMOP (a mask has nowhere to live in a cohort query); they reach a
  project through XNAT enrichment, ``upload_prostate_labels_to_xnat.py``.

``person_id`` is PI-CAI's numeric ``patient_id`` (five digits, ``10000``…), well clear of every other
mock cohort's ids: spleen/cxr persons are nine-digit NHS-number prefixes, Synthea's band starts at
100,000. Surrogate keys come from ``prostate_project``'s reserved 3,000,000 block.
"""

from __future__ import annotations

import argparse
import os
import shutil

import pandas as pd
from utils.omop_ids import surrogate_ids
from utils.omop_mappings import (
    DICOM_ATTRIBUTE_CONCEPT_CLASS_ID,
    EHR_TYPE_CONCEPT_ID,
    IMAGE_FEATURE_EVENT_FIELD_CONCEPT_ID,
    INPATIENT_VISIT_CONCEPT_ID,
    MAPPING_DICOM,
    MAPPING_MODALITY,
    MAPPING_SEX,
    MAPPING_YES_NO,
    UNKNOWN_CONCEPT_ID,
)
from utils.omop_schemas import schemas

PROJECT = "prostate_project"

# Per-dataset facts, not OMOP conventions (those live in utils.omop_mappings). Every concept below
# exists in the vocabulary the trusts load (checked against a running trust omop-db, 2026-09-02).
ANATOMIC_SITE_CONCEPT_ID = 4165732  # Prostatic structure (SNOMED)
PROCEDURE_CONCEPT_ID = 3047951  # MR Prostate WO contrast (LOINC 36519-7) — biparametric MRI is unenhanced
PROCEDURE_SOURCE_VALUE = "bpMRI prostate"
# DICOM-attribute features, as spleen publishes them. Keyed by MAPPING_DICOM's tag ids; the unit
# concept is set only where the attribute has one (millimetre for slice thickness).
DICOM_ATTRIBUTE_KEYWORDS = ["Manufacturer", "ManufacturerModelName", "SliceThickness", "Rows", "Columns"]
DICOM_ATTRIBUTE_TAGS = {
    "Manufacturer": "00080070",
    "ManufacturerModelName": "00081090",
    "SliceThickness": "00180050",
    "Rows": "00280010",
    "Columns": "00280011",
}
MILLIMETRE_CONCEPT_ID = 8588
# marksheet column → (measurement concept, unit concept, unit source value). psad has no LOINC of its
# own; "Prostate specific Ag/Prostate volume calculated from height, width and length" is the closest
# standard concept and its unit (ng/mL per mL) has no UCUM concept in the vocabulary, hence unknown.
CLINICAL_MEASUREMENTS = {
    "psa": (3013603, 8842, "ng/mL"),  # Prostate specific Ag [Mass/volume] in Serum or Plasma (LOINC 2857-1)
    "psad": (3038091, UNKNOWN_CONCEPT_ID, "ng/mL/mL"),
    "prostate_volume": (1245231, 8587, "mL"),  # Volume of prostate (SNOMED); millilitre
}
ISUP_GRADE_GROUP_CONCEPT_ID = 602257  # International Society of Urological Pathology histologic grade group
ISUP_VALUE_CONCEPTS = {1: 608744, 2: 608745, 3: 608746, 4: 608743, 5: 608747}  # grade groups 1–5 (SNOMED)
CSPCA_CONCEPT_ID = 4163261  # Malignant tumor of prostate (SNOMED); value = yes/no, as cxr's findings
PIRADS_CONCEPT_ID = 2128008964  # "Pi-rads v2.1" (DICOM value set concept, the vocabulary the trusts carry)


def source_trust_to_trust(source_trust: pd.Series) -> pd.Series:
    """``source_trust`` (1, 2, …) → the per-trust output directory name (``trust_1``, …)."""
    return "trust_" + source_trust.astype(int).astype(str)


def max_pirads(value: object) -> float:
    """The highest lesion PI-RADS of a study.

    The marksheet lists lesions comma-separated (``"3,4"``) and writes ``N/A`` (or nothing) for a
    study with no scored lesion; any token that is not a number is ignored, and a study with no
    numeric score gets NaN — no observation row, rather than a made-up one.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return float("nan")
    scores = []
    for token in str(value).split(","):
        try:
            scores.append(float(token.strip()))
        except ValueError:
            continue
    return max(scores) if scores else float("nan")


def read_sources(metadata_csv: str, marksheet_csv: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The per-series table and the per-study table (series joined with the marksheet).

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: ``(series, studies)``. ``studies`` has one row per
        ``(patient_id, study_id)``, study-level DICOM fields taken from the first series, and the
        marksheet's clinical columns. Both carry ``trust``.
    """
    series = pd.read_csv(metadata_csv, dtype=str)
    marksheet = pd.read_csv(marksheet_csv, dtype=str)
    series["trust"] = source_trust_to_trust(series["source_trust"])
    per_study = series.drop_duplicates(subset=["patient_id", "study_id"], keep="first")
    studies = per_study.merge(marksheet, on=["patient_id", "study_id"], how="left", validate="one_to_one")
    if studies["mri_date"].isna().any():
        missing = studies.loc[studies["mri_date"].isna(), ["patient_id", "study_id"]].values.tolist()
        raise SystemExit(f"{len(missing)} study(ies) have no marksheet row: {missing[:5]}")
    return series, studies.reset_index(drop=True)


def transform_metadata_to_omop_tables(
    metadata_csv: str, marksheet_csv: str, omop_root: str = "."
) -> dict[str, pd.DataFrame]:
    """Build every canonical table; write ``<omop_root>/omop/prostate_project/*.csv``; return them."""
    combined: dict[str, pd.DataFrame] = {}
    series, studies = read_sources(metadata_csv, marksheet_csv)
    study_date = pd.to_datetime(studies["StudyDate"], format="%Y%m%d")
    study_datetime = study_date  # PI-CAI carries no acquisition time

    # ---------------- Person: one per patient (a patient may have two studies) ----------------
    patients = studies.drop_duplicates(subset=["patient_id"], keep="first").reset_index(drop=True)
    patient_dates = pd.to_datetime(patients["StudyDate"], format="%Y%m%d")
    person = pd.DataFrame()
    person["person_id"] = patients["patient_id"].astype("int64")
    person["gender_concept_id"] = patients["PatientSex"].map(MAPPING_SEX).fillna(MAPPING_SEX["M"]).astype("int64")
    # Only the age at the MRI is known: the year of birth is exact to within one year, the day is not.
    person["year_of_birth"] = (patient_dates.dt.year - patients["patient_age"].astype(int)).astype("int64")
    person["race_concept_id"] = UNKNOWN_CONCEPT_ID
    person["ethnicity_concept_id"] = UNKNOWN_CONCEPT_ID
    person["gender_source_value"] = patients["PatientSex"].where(patients["PatientSex"] != "", "M")
    person["person_source_value"] = patients["patient_id"]
    person = schemas["person"].validate(person)
    print("Created and validated table: person")
    person["trust"] = patients["trust"]
    combined["person"] = person

    # ---------------- Visit + procedure: one each per study ----------------
    visit = pd.DataFrame()
    visit["visit_occurrence_id"] = surrogate_ids(PROJECT, len(studies))
    visit["person_id"] = studies["patient_id"].astype("int64")
    visit["visit_concept_id"] = INPATIENT_VISIT_CONCEPT_ID  # as spleen/cxr; https://athena.ohdsi.org/search-terms/terms/9201
    visit["visit_start_date"] = study_date
    visit["visit_start_datetime"] = study_datetime
    visit["visit_end_date"] = study_date
    visit["visit_end_datetime"] = study_datetime
    visit["visit_type_concept_id"] = EHR_TYPE_CONCEPT_ID
    visit = schemas["visit_occurrence"].validate(visit)
    print("Created and validated table: visit_occurrence")
    visit["trust"] = studies["trust"]
    combined["visit_occurrence"] = visit

    procedure = pd.DataFrame()
    procedure["procedure_occurrence_id"] = surrogate_ids(PROJECT, len(studies))
    procedure["person_id"] = visit["person_id"]
    procedure["procedure_concept_id"] = PROCEDURE_CONCEPT_ID
    procedure["procedure_date"] = study_date
    procedure["procedure_datetime"] = study_datetime
    procedure["procedure_type_concept_id"] = EHR_TYPE_CONCEPT_ID
    procedure["quantity"] = 1
    procedure["visit_occurrence_id"] = visit["visit_occurrence_id"]
    procedure["procedure_source_value"] = PROCEDURE_SOURCE_VALUE
    procedure = schemas["procedure_occurrence"].validate(procedure)
    print("Created and validated table: procedure_occurrence")
    procedure["trust"] = studies["trust"]
    combined["procedure_occurrence"] = procedure

    # ---------------- Image occurrence: one per SERIES (t2w, adc, hbv of a study) ----------------
    study_keys = studies[["patient_id", "study_id"]].copy()
    study_keys["visit_occurrence_id"] = visit["visit_occurrence_id"].values
    study_keys["procedure_occurrence_id"] = procedure["procedure_occurrence_id"].values
    series = series.merge(study_keys, on=["patient_id", "study_id"], how="left", validate="many_to_one")
    series_date = pd.to_datetime(series["StudyDate"], format="%Y%m%d")
    image = pd.DataFrame()
    image["image_occurrence_id"] = surrogate_ids(PROJECT, len(series))
    image["person_id"] = series["patient_id"].astype("int64")
    image["procedure_occurrence_id"] = series["procedure_occurrence_id"].astype("int64")
    image["visit_occurrence_id"] = series["visit_occurrence_id"].astype("int64")
    image["anatomic_site_concept_id"] = ANATOMIC_SITE_CONCEPT_ID
    image["local_path"] = "./dicom/" + series["patient_id"] + "/" + series["study_id"] + "/" + series["modality"]
    image["image_occurrence_date"] = series_date
    image["image_study_uid"] = series["StudyInstanceUID"]
    image["image_series_uid"] = series["SeriesInstanceUID"]
    image["modality_concept_id"] = series["Modality"].map(MAPPING_MODALITY).astype("int64")
    image["accession_id"] = series["AccessionNumber"]
    image = schemas["image_occurrence"].validate(image)
    print("Created and validated table: image_occurrence")
    image["trust"] = series["trust"]
    combined["image_occurrence"] = image

    # ---------------- DICOM attributes → image_feature + measurement (as spleen) ----------------
    feat = pd.concat(
        [
            image[["person_id", "visit_occurrence_id", "image_occurrence_id", "image_occurrence_date", "trust"]],
            series[DICOM_ATTRIBUTE_KEYWORDS],
        ],
        axis=1,
    ).rename(columns={**DICOM_ATTRIBUTE_TAGS, "image_occurrence_date": "measurement_date"})
    feat = feat.melt(
        id_vars=["trust", "person_id", "visit_occurrence_id", "image_occurrence_id", "measurement_date"],
        var_name="measurement_source_value",
        value_name="value_source_value",
    )
    feat["measurement_concept_id"] = feat["measurement_source_value"].map(MAPPING_DICOM).astype("int64")
    feat["unit_concept_id"] = UNKNOWN_CONCEPT_ID
    feat["unit_source_value"] = None
    is_thickness = feat["measurement_source_value"].eq(DICOM_ATTRIBUTE_TAGS["SliceThickness"])
    feat.loc[is_thickness, "unit_concept_id"] = MILLIMETRE_CONCEPT_ID
    feat.loc[is_thickness, "unit_source_value"] = "millimeter"

    # ---------------- Marksheet → clinical measurements ----------------
    clinical_rows = []
    for column, (concept_id, unit_concept_id, unit_source_value) in CLINICAL_MEASUREMENTS.items():
        values = pd.to_numeric(studies[column], errors="coerce")
        keep = values.notna()
        clinical_rows.append(
            pd.DataFrame(
                {
                    "trust": studies.loc[keep, "trust"],
                    "person_id": studies.loc[keep, "patient_id"].astype("int64"),
                    "visit_occurrence_id": visit.loc[keep, "visit_occurrence_id"],
                    "measurement_date": study_date[keep],
                    "measurement_source_value": column,
                    "value_source_value": studies.loc[keep, column],
                    "measurement_concept_id": concept_id,
                    "unit_concept_id": unit_concept_id,
                    "unit_source_value": unit_source_value,
                }
            )
        )
    clinical = pd.concat(clinical_rows, ignore_index=True)

    all_measurements = pd.concat([feat, clinical], ignore_index=True)
    all_measurements["measurement_id"] = surrogate_ids(PROJECT, len(all_measurements))
    all_measurements["measurement_type_concept_id"] = EHR_TYPE_CONCEPT_ID
    all_measurements["value_as_number"] = pd.to_numeric(all_measurements["value_source_value"], errors="coerce")
    all_measurements["value_source_value"] = all_measurements["value_source_value"].astype(str)
    all_measurements["unit_concept_id"] = all_measurements["unit_concept_id"].astype("int64")

    # The DICOM-attribute measurements are also image features of their series (the spleen pattern:
    # the image_feature id IS the measurement id); the clinical ones belong to the visit, not an image.
    dicom_part = all_measurements.iloc[: len(feat)]
    image_feature = pd.DataFrame()
    image_feature["image_feature_id"] = dicom_part["measurement_id"].values
    image_feature["person_id"] = dicom_part["person_id"].values
    image_feature["image_occurrence_id"] = feat["image_occurrence_id"].astype("int64").values
    image_feature["image_feature_event_field_concept_id"] = IMAGE_FEATURE_EVENT_FIELD_CONCEPT_ID
    image_feature["image_feature_event_id"] = dicom_part["measurement_id"].values
    image_feature["image_feature_concept_id"] = dicom_part["measurement_concept_id"].values
    image_feature["image_feature_type_concept_id"] = DICOM_ATTRIBUTE_CONCEPT_CLASS_ID
    image_feature["anatomic_site_concept_id"] = ANATOMIC_SITE_CONCEPT_ID
    image_feature = schemas["image_feature"].validate(image_feature)
    print("Created and validated table: image_feature")
    image_feature["trust"] = feat["trust"].values
    combined["image_feature"] = image_feature

    measurement = all_measurements[
        [
            "measurement_id",
            "person_id",
            "measurement_concept_id",
            "measurement_date",
            "measurement_type_concept_id",
            "value_as_number",
            "unit_concept_id",
            "visit_occurrence_id",
            "measurement_source_value",
            "unit_source_value",
            "value_source_value",
        ]
    ].copy()
    measurement = schemas["measurement"].validate(measurement)
    print("Created and validated table: measurement")
    measurement["trust"] = all_measurements["trust"].values
    combined["measurement"] = measurement

    # ---------------- Marksheet → observations (grade group, csPCa, PI-RADS) ----------------
    isup = pd.to_numeric(studies["case_ISUP"], errors="coerce")
    cspca = studies["case_csPCa"].str.strip().str.upper()
    pirads = studies["lesion_PIRADS"].map(max_pirads)
    base = {
        "trust": studies["trust"],
        "person_id": studies["patient_id"].astype("int64"),
        "visit_occurrence_id": visit["visit_occurrence_id"],
        "observation_date": study_date,
    }
    obs_frames = [
        pd.DataFrame(
            {
                **base,
                "observation_concept_id": ISUP_GRADE_GROUP_CONCEPT_ID,
                "value_as_number": isup.astype(float),
                "value_as_concept_id": isup.map(ISUP_VALUE_CONCEPTS).fillna(UNKNOWN_CONCEPT_ID).astype("int64"),
                "observation_source_value": "case_ISUP",
                "value_source_value": studies["case_ISUP"],
            }
        )[isup.notna()],
        pd.DataFrame(
            {
                **base,
                "observation_concept_id": CSPCA_CONCEPT_ID,
                "value_as_number": cspca.map({"YES": 1.0, "NO": 0.0}),
                "value_as_concept_id": cspca.map({"YES": MAPPING_YES_NO["yes"], "NO": MAPPING_YES_NO["no"]}),
                "observation_source_value": "case_csPCa",
                "value_source_value": studies["case_csPCa"],
            }
        )[cspca.isin(["YES", "NO"])],
        pd.DataFrame(
            {
                **base,
                "observation_concept_id": PIRADS_CONCEPT_ID,
                "value_as_number": pirads,
                "value_as_concept_id": UNKNOWN_CONCEPT_ID,
                "observation_source_value": "lesion_PIRADS",
                "value_source_value": studies["lesion_PIRADS"],
            }
        )[pirads.notna()],
    ]
    observation = pd.concat(obs_frames, ignore_index=True)
    observation["observation_id"] = surrogate_ids(PROJECT, len(observation))
    observation["observation_type_concept_id"] = EHR_TYPE_CONCEPT_ID
    observation["value_as_concept_id"] = observation["value_as_concept_id"].astype("int64")
    observation["value_source_value"] = observation["value_source_value"].astype(str)
    trust_col = observation.pop("trust")
    observation = schemas["observation"].validate(observation)
    print("Created and validated table: observation")
    observation["trust"] = trust_col.values
    combined["observation"] = observation

    out_dir = os.path.join(omop_root, "omop", PROJECT)
    os.makedirs(out_dir, exist_ok=True)
    for name, table in combined.items():
        table.to_csv(os.path.join(out_dir, f"{name}.csv"), index=False)
    return combined


def split_data_into_trusts(
    combined: dict[str, pd.DataFrame], omop_root: str = "."
) -> dict[str, dict[str, pd.DataFrame]]:
    """``omop/<trust>/prostate_project/<table>.csv`` per trust — the layout the gate and ``dataset build`` read."""
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
    parser.add_argument("--metadata", default="data/prostate/source/dicom_metadata.csv")
    parser.add_argument("--marksheet", default="data/prostate/source/marksheet.csv")
    parser.add_argument("--omop-root", default=".", help="writes <omop-root>/omop/prostate_project and omop/<trust>/")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    tables = transform_metadata_to_omop_tables(args.metadata, args.marksheet, args.omop_root)
    print("Created combined tables and saved to CSV.")
    split = split_data_into_trusts(tables, args.omop_root)
    for trust, part in split.items():
        print(f"{trust}: {len(part['person'])} persons, {len(part['image_occurrence'])} series")
