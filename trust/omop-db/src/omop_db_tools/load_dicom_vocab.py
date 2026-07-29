# Adapted from https://github.com/paulnagy/DICOM2OMOP/blob/main/dicom_standard_to_omop/load_dicom_to_omop.ipynb

import zipfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from config import get_settings
from sqlalchemy import create_engine, text

VOCAB_ZIP_PATH = Path("data/vocab_dicom_paulnagy_20260109.zip")
VOCAB_FILES_PATH = Path("data/vocab_dicom_paulnagy_20260109")

print("🩻 Loading DICOM vocabulary tables...")

# ----------------------------------------------------------------------
# Ensure DICOM vocabulary directory exists (unzip if needed)
# ----------------------------------------------------------------------
if not VOCAB_FILES_PATH.exists() or not any(VOCAB_FILES_PATH.iterdir()):
    print(f"Extracting {VOCAB_ZIP_PATH} → {VOCAB_FILES_PATH} ...")

    with zipfile.ZipFile(VOCAB_ZIP_PATH, "r") as zip_ref:
        zip_ref.extractall((VOCAB_FILES_PATH.parent))

    print("Extraction complete!")
else:
    print(f"Using existing directory: {VOCAB_FILES_PATH}")

# ----------------------------------------------------------------------
# Database connection
# ----------------------------------------------------------------------
# Create sync engine for pandas
engine = create_engine(get_settings().OMOP_DATABASE_URL.get_secret_value(), echo=False)


# ----------------------------------------------------------------------
# Helper: idempotent insert (avoids duplicates)
# ----------------------------------------------------------------------
def safe_insert(table, df, engine):
    """
    Inserts rows with ON CONFLICT DO NOTHING.
    Works for OMOP vocab tables that always have primary keys.
    """
    with engine.begin() as conn:
        for _, row in df.iterrows():
            cols = ", ".join(row.index)
            placeholders = ", ".join([f":{col}" for col in row.index])
            update_sql = text(f"INSERT INTO omop.{table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING")
            conn.execute(update_sql, row.to_dict())


# --------------------------------------------------------------------------
# 0. Insert the missing VOCABULARY and CONCEPT CLASS CONCEPTS (2128000000-2)
# --------------------------------------------------------------------------
print("Ensuring DICOM vocabulary and concept class concepts exist (2128000000-2)...")

df_vocab_concept = pd.DataFrame([
    {
        "concept_id": 2128000000,
        "concept_name": "Digital Imaging and Communications in Medicine (DICOM)",
        "domain_id": "Metadata",
        "vocabulary_id": "Vocabulary",
        "concept_class_id": "Vocabulary",
        "standard_concept": None,
        "concept_code": "DICOM",
        "valid_start_date": date(1970, 1, 1),
        "valid_end_date": date(2099, 12, 31),
        "invalid_reason": None,
    },
    {
        "concept_id": 2128000001,
        "concept_name": "DICOM Attributes",
        "domain_id": "Metadata",
        "vocabulary_id": "Concept Class",
        "concept_class_id": "Concept Class",
        "standard_concept": None,
        "concept_code": "DICOM",
        "valid_start_date": date(1970, 1, 1),
        "valid_end_date": date(2099, 12, 31),
        "invalid_reason": None,
    },
    {
        "concept_id": 2128000002,
        "concept_name": "DICOM Value Sets",
        "domain_id": "Metadata",
        "vocabulary_id": "Concept Class",
        "concept_class_id": "Concept Class",
        "standard_concept": None,
        "concept_code": "DICOM",
        "valid_start_date": date(1970, 1, 1),
        "valid_end_date": date(2099, 12, 31),
        "invalid_reason": None,
    },
])

safe_insert("CONCEPT", df_vocab_concept, engine)

# ----------------------------------------------------------------------
# 1. Load VOCABULARY
# ----------------------------------------------------------------------
print("Loading DICOM VOCABULARY...")

df_vocab = pd.DataFrame([
    {
        "vocabulary_id": "DICOM",
        "vocabulary_name": "Digital Imaging and Communications in Medicine (NEMA)",
        "vocabulary_reference": "https://www.dicomstandard.org/current",
        "vocabulary_version": "NEMA Standard PS3",
        "vocabulary_concept_id": 2128000000,
    }
])

safe_insert("VOCABULARY", df_vocab, engine)

# ----------------------------------------------------------------------
# 2. Load CONCEPT_CLASS
# ----------------------------------------------------------------------
print("Loading DICOM CONCEPT_CLASS...")

df_classes = pd.DataFrame([
    {
        "concept_class_id": "DICOM Attributes",
        "concept_class_name": "DICOM Attributes",
        "concept_class_concept_id": 2128000001,
    },
    {
        "concept_class_id": "DICOM Value Sets",
        "concept_class_name": "DICOM Value Sets",
        "concept_class_concept_id": 2128000002,
    },
])

safe_insert("CONCEPT_CLASS", df_classes, engine)

# ----------------------------------------------------------------------
# 3. Load DICOM CONCEPT definitions
# ----------------------------------------------------------------------
print("Loading DICOM CONCEPT definitions...")

concepts_path = f"{VOCAB_FILES_PATH}/omop_table_staging_v5.csv"
omop_table_staging = pd.read_csv(concepts_path)

# Convert dates
omop_table_staging["valid_start_date"] = pd.to_datetime(
    omop_table_staging["valid_start_date"].astype(str), format="%Y%m%d", errors="coerce"
)
omop_table_staging["valid_end_date"] = pd.to_datetime(
    omop_table_staging["valid_end_date"].astype(str), format="%Y%m%d", errors="coerce"
)

# FIX: OMOP VARCHAR(1) fields should be NULL for DICOM
omop_table_staging["standard_concept"] = None
omop_table_staging["invalid_reason"] = None

safe_insert("CONCEPT", omop_table_staging, engine)

# ----------------------------------------------------------------------
# 4. Load CONCEPT_RELATIONSHIP (3 files merged)
# ----------------------------------------------------------------------
print("Loading DICOM relationships...")

# 1. Attributes-CID-ValueSets
concept_relationship_staging = pd.read_pickle(f"{VOCAB_FILES_PATH}/part3_to_part16_relationship_via_CID.pkl")

# 2. Attributes-Code String values
cs_values_maps_to_value = pd.read_csv(f"{VOCAB_FILES_PATH}/cs_values_maps_to_value.csv")

# 3. DICOM Code String to OMOP Standard coding systems
cs_values_maps_to = pd.read_csv(f"{VOCAB_FILES_PATH}/cs_values_maps_to.csv")


# Remove auto-generated index columns
def clean(df):
    return df.loc[:, ~df.columns.str.contains("^Unnamed")]


concept_relationship_staging = clean(concept_relationship_staging)
# cs_values_maps_to_value = clean(cs_values_maps_to_value)
cs_values_maps_to = clean(cs_values_maps_to)


# Normalise into unified structure
def convert_dates(df):
    df["valid_start_date"] = datetime.strptime("19930101", "%Y%m%d").date()
    df["valid_end_date"] = datetime.strptime("20991231", "%Y%m%d").date()
    return df


concept_relationship_staging = convert_dates(concept_relationship_staging)
cs_values_maps_to_value = convert_dates(cs_values_maps_to_value)
cs_values_maps_to = convert_dates(cs_values_maps_to)

df_relationships = pd.concat(
    [concept_relationship_staging, cs_values_maps_to_value, cs_values_maps_to], ignore_index=True
)

safe_insert("CONCEPT_RELATIONSHIP", df_relationships, engine)

print("DICOM vocabulary load complete!")
