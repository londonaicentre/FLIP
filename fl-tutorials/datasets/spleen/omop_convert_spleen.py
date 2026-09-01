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

"""Build the spleen_project OMOP tables from the DICOM metadata table.

Imported from ``londonaicentre/flip-omop-mock-data`` (FLIP#1092). Behaviour is unchanged, including
the round-robin trust split — MSD Task09_Spleen is single-source, so the two trusts carry no
acquisition heterogeneity. Replacing that with a real site split is FLIP#1092 follow-up 2.
"""

import os
import shutil

import pandas as pd
from pydicom.datadict import tag_for_keyword
from utils.omop_mappings import (
    MAPPING_DICOM,
    MAPPING_MODALITY,
    MAPPING_PROCEDURE_TYPE,
    MAPPING_SEX,
)
from utils.omop_schemas import schemas

TRUSTS = {
    "trust_1": ("Dummy"),
    "trust_2": ("Dummy"),
}
DICOM_FOLDER_PATH = "./dicom_output"
PROJECT = "spleen_project"


def nhs_number_to_integer(s):
    """Converts the PatientID (NHS number e.g. '123 456 789') to an integer"""
    return int(f"{s.replace(' ', '')[:9]}")


def transform_dicom_metadata_to_omop_tables(
    csv_file_path: str,
):
    """
    Convert a dicom_metadata.csv file to OMOP tables
    Returns a dictionary of OMOP tables by name.
    """
    combined_tables = {}

    # Read all CSV columns as strings to avoid issues with leading zeros
    df = pd.read_csv(csv_file_path, dtype=str)

    # Split the data into trusts
    trust_key_mapping = {number: trust for number, trust in enumerate(TRUSTS.keys())}
    df["trust_index"] = df.index % len(TRUSTS)
    df["trust"] = df["trust_index"].map(trust_key_mapping)

    # ----------------------------------------------
    # ---------------- Person table ----------------
    # ----------------------------------------------
    person = pd.DataFrame()
    person["person_id"] = df["PatientID"].apply(nhs_number_to_integer)  # Could assign internal integer
    person["gender_concept_id"] = df["PatientSex"].map(MAPPING_SEX)
    person["birth_datetime"] = pd.to_datetime(df["PatientBirthDate"], format="%Y%m%d")
    person["year_of_birth"] = person["birth_datetime"].dt.year.astype(
        "int64"
    )  # pandas default is int32 but pandera expects int64
    person["month_of_birth"] = person["birth_datetime"].dt.month.astype("int64")
    person["day_of_birth"] = person["birth_datetime"].dt.day.astype("int64")
    person["race_concept_id"] = 0  # set as unknown
    person["ethnicity_concept_id"] = 0  # set as unknown
    person["gender_source_value"] = df["PatientSex"]
    person["person_source_value"] = df["PatientID"]

    # Validate, add trust column, and store
    person = schemas["person"].validate(person)
    print("Created and validated table: person")
    person["trust"] = df["trust"]
    combined_tables["person"] = person

    # ----------------------------------------------
    # --------- Visit Occurrence table -------------
    # ----------------------------------------------
    visit_occurrence = pd.DataFrame()
    visit_occurrence["visit_occurrence_id"] = range(2000001, len(df) + 2000001)
    visit_occurrence["person_id"] = person["person_id"]
    visit_occurrence["visit_concept_id"] = 9201  # Inpatient visit https://athena.ohdsi.org/search-terms/terms/9201
    visit_occurrence["visit_start_date"] = pd.to_datetime(df["StudyDate"], format="%Y%m%d")
    df["StudyTime"] = [f"{int(i):06d}" for i in list(df["StudyTime"])]
    visit_occurrence["visit_start_datetime"] = pd.to_datetime(df["StudyDate"] + df["StudyTime"], format="%Y%m%d%H%M%S")
    visit_occurrence["visit_end_date"] = visit_occurrence["visit_start_date"]
    visit_occurrence["visit_end_datetime"] = visit_occurrence["visit_start_datetime"]
    visit_occurrence["visit_type_concept_id"] = (
        32817  # EHR, copying https://github.com/paulnagy/DICOM2OMOP/blob/main/demonstration/transform_imaging_metadata.ipynb
    )

    # Validate, add trust column, and store
    visit_occurrence = schemas["visit_occurrence"].validate(visit_occurrence)
    print("Created and validated table: visit_occurrence")
    visit_occurrence["trust"] = df["trust"]
    combined_tables["visit_occurrence"] = visit_occurrence

    # ----------------------------------------------
    # --------- Procedure Occurrence table ---------
    # ----------------------------------------------
    procedure_occurrence = pd.DataFrame()
    procedure_occurrence["procedure_occurrence_id"] = range(2000001, len(df) + 2000001)
    procedure_occurrence["person_id"] = person["person_id"]
    procedure_occurrence["procedure_concept_id"] = df["StudyDescription"].str.lower().map(MAPPING_PROCEDURE_TYPE)
    procedure_occurrence["procedure_date"] = pd.to_datetime(df["StudyDate"], format="%Y%m%d")
    procedure_occurrence["procedure_datetime"] = pd.to_datetime(
        df["StudyDate"] + df["StudyTime"], format="%Y%m%d%H%M%S"
    )
    procedure_occurrence["procedure_type_concept_id"] = 32817  # EHR as per https://doi.org/10.1007/s10278-024-00982-6
    procedure_occurrence["quantity"] = 1  # Could be number of series if available?
    procedure_occurrence["visit_occurrence_id"] = visit_occurrence["visit_occurrence_id"]
    procedure_occurrence["procedure_source_value"] = df["StudyDescription"]

    # Validate, add trust column, and store
    procedure_occurrence = schemas["procedure_occurrence"].validate(procedure_occurrence)
    print("Created and validated table: procedure_occurrence")
    procedure_occurrence["trust"] = df["trust"]
    combined_tables["procedure_occurrence"] = procedure_occurrence

    # ----------------------------------------------
    # --------- Image Occurrence table -------------
    # ----------------------------------------------
    image_occurrence = pd.DataFrame()
    image_occurrence["image_occurrence_id"] = range(2000001, len(df) + 2000001)
    image_occurrence["person_id"] = person["person_id"]
    image_occurrence["procedure_occurrence_id"] = procedure_occurrence["procedure_occurrence_id"]
    image_occurrence["visit_occurrence_id"] = visit_occurrence["visit_occurrence_id"]
    image_occurrence["anatomic_site_concept_id"] = 4302605  # not read from DICOM; hardcoded (splenic structure)
    # In spleen_metadata.csv, 'FilePath' contains the full path to the DICOM file,
    # so use `dirname` to get the folder path
    image_occurrence["local_path"] = df["FilePath"].apply(os.path.dirname)
    image_occurrence["image_occurrence_date"] = pd.to_datetime(df["StudyDate"], format="%Y%m%d")
    image_occurrence["image_study_uid"] = df["StudyInstanceUID"]
    image_occurrence["image_series_uid"] = df["SeriesInstanceUID"]
    image_occurrence["modality_concept_id"] = df["Modality"].map(MAPPING_MODALITY)
    image_occurrence["accession_id"] = df[
        "AccessionNumber"
    ]  # Not in the official OMOP schema; hope to retire later and use StudyInstanceUID instead

    # Validate, add trust column, and store
    image_occurrence = schemas["image_occurrence"].validate(image_occurrence)
    print("Created and validated table: image_occurrence")
    image_occurrence["trust"] = df["trust"]
    combined_tables["image_occurrence"] = image_occurrence

    # ----------------------------------------------
    # ---- Image Feature and Measurement tables ----
    # ----------------------------------------------
    # Expand out DICOM attributes into long format
    include_dicom_attribute_keywords = ["Manufacturer", "ManufacturerModelName", "SliceThickness", "Rows", "Columns"]
    include_dicom_attribute_tags = [f"{tag_for_keyword(kw):08x}" for kw in include_dicom_attribute_keywords]
    df_feat = pd.concat(
        [
            person[["person_id"]],
            visit_occurrence[["visit_occurrence_id"]],
            image_occurrence[["image_occurrence_id", "image_occurrence_date", "anatomic_site_concept_id"]],
            df[include_dicom_attribute_keywords + ["trust"]],
        ],
        axis=1,
    )
    df_feat.rename(columns=dict(zip(include_dicom_attribute_keywords, include_dicom_attribute_tags)), inplace=True)
    df_feat.rename(columns={"image_occurrence_date": "measurement_date"}, inplace=True)
    my_id_vars = [
        "trust",
        "person_id",
        "visit_occurrence_id",
        "image_occurrence_id",
        "anatomic_site_concept_id",
        "measurement_date",
    ]
    df_feat = df_feat.melt(id_vars=my_id_vars, var_name="measurement_source_value", value_name="value_source_value")
    df_feat["measurement_concept_id"] = df_feat["measurement_source_value"].map(MAPPING_DICOM)
    df_feat["image_feature_id"] = range(2000001, len(df_feat) + 2000001)

    # Image Feature
    c = ["image_feature_id", "person_id", "image_occurrence_id"]
    image_feature = df_feat[c].copy()
    image_feature["image_feature_event_field_concept_id"] = 1147330
    image_feature["image_feature_event_id"] = df_feat[
        "image_feature_id"
    ].copy()  # in this case, the measurement_id is the same
    image_feature["image_feature_concept_id"] = df_feat["measurement_concept_id"].copy()
    image_feature["image_feature_type_concept_id"] = (
        2128000001  # Custom concept class code for DICOM Attributes https://github.com/paulnagy/DICOM2OMOP/blob/main/dicom_standard_to_omop/load_dicom_to_omop.ipynb
    )
    image_feature["anatomic_site_concept_id"] = 4302605  # splenic structure
    # Validate, add trust column, and store
    image_feature = schemas["image_feature"].validate(image_feature)
    print("Created and validated table: image_feature")
    image_feature["trust"] = df_feat["trust"]
    combined_tables["image_feature"] = image_feature

    c = ["person_id", "measurement_concept_id", "measurement_date"]
    measurement = df_feat[c].copy()
    measurement["measurement_id"] = df_feat["image_feature_id"].copy()
    measurement["measurement_type_concept_id"] = 32817  # EHR as per https://doi.org/10.1007/s10278-024-00982-6
    measurement["value_as_number"] = pd.to_numeric(df_feat["value_source_value"], errors="coerce")
    measurement["unit_concept_id"] = 0  # default to unknown, otherwise filled with NaN and dtype=float
    measurement.loc[measurement["measurement_concept_id"].eq(2128100808), "unit_concept_id"] = (
        8588  # SliceThickness in millimeter
    )
    measurement["visit_occurrence_id"] = df_feat["visit_occurrence_id"].copy()
    measurement["measurement_source_value"] = df_feat["measurement_source_value"].copy()
    measurement.loc[measurement["measurement_concept_id"].eq(2128100808), "unit_source_value"] = "millimeter"
    measurement["value_source_value"] = df_feat["value_source_value"].astype(str)
    # Validate, add trust column, and store
    measurement = schemas["measurement"].validate(measurement)
    print("Created and validated table: measurement")
    measurement["trust"] = df_feat["trust"]
    combined_tables["measurement"] = measurement

    # ----------------------------------------------
    # ---------------- Finish up  ------------------
    # ----------------------------------------------

    # Save the tables to CSV files
    os.makedirs(f"omop/{PROJECT}", exist_ok=True)
    for table_name, table_df in combined_tables.items():
        table_df.to_csv(f"omop/{PROJECT}/{table_name}.csv", index=False)

    return combined_tables


def split_data_into_trusts_and_copy_dicoms(
    combined_tables: dict,
    COPY_DICOM: bool = False,
):
    """
    Split the data into trust specific folders.
        * DICOMs are copied to the trust specific folders.
        * The person and radiology CSVs are split and saved into trust specific folders.
    """
    tables_by_trust: dict[str, dict[str, pd.DataFrame]] = {trust: {} for trust in TRUSTS.keys()}

    for trust in TRUSTS.keys():
        trust_folder_name = f"omop/{trust}/{PROJECT}"
        trust_dicom_folder_name = f"{trust_folder_name}/dicoms"

        # Delete the trust folder if it already exists
        if os.path.isdir(trust_folder_name):
            shutil.rmtree(trust_folder_name)

        os.makedirs(trust_folder_name)

        # Split tables and save to CSV
        for table_name, combined_table in combined_tables.items():
            trust_table = combined_table.loc[combined_table["trust"].eq(trust), :].drop(columns="trust")
            schemas[table_name].validate(trust_table)
            trust_table.to_csv(f"{trust_folder_name}/{table_name}.csv", index=False)
            tables_by_trust[trust][table_name] = trust_table

        if COPY_DICOM:
            os.makedirs(trust_dicom_folder_name)
            image_occurrence = tables_by_trust[trust]["image_occurrence"]
            for _, row in image_occurrence.iterrows():
                # Retrieve and check source DICOM folder path
                subject_dicom_source_folder = str(row["local_path"])
                if not subject_dicom_source_folder:
                    raise ValueError("Dicom folder path is blank")
                if not os.path.isdir(subject_dicom_source_folder):
                    raise FileNotFoundError(f"Source DICOM folder not found: {subject_dicom_source_folder}")

                # Construct and check subject_name
                subject_name = os.path.basename(subject_dicom_source_folder)
                if not subject_name:
                    raise ValueError(
                        f"Subject name could not be determined from DICOM folder path: {subject_dicom_source_folder}"
                    )

                # Retrieve accession_id and confirm it is not blank
                accession_id = row["accession_id"]
                if not accession_id:
                    raise ValueError(f"Accession ID is blank for {subject_dicom_source_folder}")

                # Copy all dicoms for this subject to the trust dicom folder under a subject folder
                trust_subject_dicom_folder_name = f"{trust_dicom_folder_name}/{accession_id}_{subject_name}"
                # subject_dicom_source_folder by definition is a folder not a file
                shutil.copytree(subject_dicom_source_folder, trust_subject_dicom_folder_name)
                print(
                    f"Copied dicoms from {accession_id} ({subject_dicom_source_folder}) to "
                    f"{trust_subject_dicom_folder_name}"
                )

            trust_dicom_count = len(os.listdir(trust_dicom_folder_name))
            print(f"trust_dicom_count: {trust_dicom_count}")
            print(f"dicom datasets expected: {len(image_occurrence)}")

    return tables_by_trust


if __name__ == "__main__":
    combined_tables = transform_dicom_metadata_to_omop_tables("data/spleen_metadata.csv")
    print("Created combined tables and saved to CSV.")

    tables_by_trust = split_data_into_trusts_and_copy_dicoms(combined_tables, COPY_DICOM=False)
    print("Split data into trusts and saved to CSV.")

    # upload_trust_data_to_orthanc(clear_orthanc=True)

    # Review results
    for trust, tables in tables_by_trust.items():
        for table_name in ["person", "visit_occurrence", "procedure_occurrence", "image_occurrence"]:
            print(f"Trust: {trust}. Table: {table_name}.")
            print(tables[table_name].head())
            print("")
