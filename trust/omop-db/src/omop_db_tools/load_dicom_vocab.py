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
#
"""Load the DICOM vocabulary (NEMA PS3, freely redistributable) into OMOP.

Adapted from https://github.com/paulnagy/DICOM2OMOP (Apache 2.0), specifically
dicom_standard_to_omop/load_dicom_to_omop.ipynb — see THIRD_PARTY_NOTICES.md.

The vocab bundle (see ``make fetch-vocab-dicom``) ships plain CSVs only; the
upstream notebook's pickled relationship frame is converted to CSV when the
bundle is published, so nothing here deserialises pickles.
"""

import argparse
import zipfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine, text

from omop_db_tools.config import get_settings
from omop_db_tools.import_tables import validate_identifier

DEFAULT_VOCAB_DIR = Path("data/vocab_dicom_paulnagy_20260109")

REQUIRED_VOCAB_FILES = (
    "omop_table_staging_v5.csv",
    "cs_values_maps_to.csv",
    "cs_values_maps_to_value.csv",
    "part3_to_part16_relationship_via_CID.csv",
)


def safe_insert(table: str, df: pd.DataFrame, engine: Engine) -> None:
    """Insert rows with ON CONFLICT DO NOTHING, reporting inserted vs skipped.

    Duplicate-skipping relies on a primary key or unique constraint on the
    target table (true for CONCEPT, VOCABULARY and CONCEPT_CLASS in this
    schema). CONCEPT_RELATIONSHIP carries no unique constraint, so re-running
    against an already-loaded database duplicates relationship rows — the
    populate pipeline only ever targets freshly initialised databases.

    Args:
        table (str): Target table name in the omop schema.
        df (pd.DataFrame): Rows to insert; column names must be plain SQL identifiers.
        engine (Engine): Target database engine.
    """
    table = validate_identifier(table)
    columns = [validate_identifier(column) for column in df.columns]
    cols = ", ".join(columns)
    placeholders = ", ".join([f":{column}" for column in columns])
    insert_sql = text(f"INSERT INTO omop.{table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING")
    inserted = 0
    with engine.begin() as conn:
        for _, row in df.iterrows():
            inserted += conn.execute(insert_sql, row.to_dict()).rowcount
    print(f"   omop.{table}: inserted={inserted} skipped={len(df) - inserted}")


def ensure_vocab_dir(vocab_dir: Path) -> None:
    """Ensure the DICOM vocabulary directory exists and is complete, extracting its sibling zip if needed.

    Raises:
        FileNotFoundError: When neither the directory nor its zip exists, or the
            directory (pre-existing or freshly extracted) is missing expected
            CSVs — e.g. after an interrupted extraction or a repacked bundle
            whose top-level directory name doesn't match.
    """
    if not (vocab_dir.exists() and any(vocab_dir.iterdir())):
        vocab_zip = Path(f"{vocab_dir}.zip")
        if not vocab_zip.is_file():
            raise FileNotFoundError(f"Neither {vocab_dir} nor {vocab_zip} exists — run `make fetch-vocab-dicom` first.")
        print(f"Extracting {vocab_zip} → {vocab_dir} ...")
        with zipfile.ZipFile(vocab_zip, "r") as zip_ref:
            zip_ref.extractall(vocab_dir.parent)
        print("Extraction complete!")
    else:
        print(f"Using existing directory: {vocab_dir}")
    missing = [name for name in REQUIRED_VOCAB_FILES if not (vocab_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"{vocab_dir} is missing {missing} — delete the directory and its .zip, "
            "then re-run `make fetch-vocab-dicom`"
        )


def load_vocabulary_metadata(engine: Engine) -> None:
    """Insert the DICOM VOCABULARY / CONCEPT_CLASS scaffolding concepts (2128000000-2)."""
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


def load_concepts(engine: Engine, vocab_dir: Path) -> None:
    """Load the DICOM CONCEPT definitions from the staging CSV."""
    print("Loading DICOM CONCEPT definitions...")
    omop_table_staging = pd.read_csv(vocab_dir / "omop_table_staging_v5.csv")

    # errors="raise": a malformed date should name the offending value here, not
    # surface later as an opaque NOT NULL / adaptation failure inside the insert.
    omop_table_staging["valid_start_date"] = pd.to_datetime(
        omop_table_staging["valid_start_date"].astype(str), format="%Y%m%d", errors="raise"
    )
    omop_table_staging["valid_end_date"] = pd.to_datetime(
        omop_table_staging["valid_end_date"].astype(str), format="%Y%m%d", errors="raise"
    )

    # FIX: OMOP VARCHAR(1) fields should be NULL for DICOM
    omop_table_staging["standard_concept"] = None
    omop_table_staging["invalid_reason"] = None

    safe_insert("CONCEPT", omop_table_staging, engine)


def _drop_index_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.loc[:, ~df.columns.str.contains("^Unnamed")]


def _fix_relationship_dates(df: pd.DataFrame) -> pd.DataFrame:
    df["valid_start_date"] = datetime.strptime("19930101", "%Y%m%d").date()
    df["valid_end_date"] = datetime.strptime("20991231", "%Y%m%d").date()
    return df


def load_relationships(engine: Engine, vocab_dir: Path) -> None:
    """Load CONCEPT_RELATIONSHIP rows from the three relationship CSVs."""
    print("Loading DICOM relationships...")

    # 1. Attributes-CID-ValueSets
    concept_relationship_staging = pd.read_csv(vocab_dir / "part3_to_part16_relationship_via_CID.csv")
    # 2. Attributes-Code String values
    cs_values_maps_to_value = pd.read_csv(vocab_dir / "cs_values_maps_to_value.csv")
    # 3. DICOM Code String to OMOP Standard coding systems
    cs_values_maps_to = pd.read_csv(vocab_dir / "cs_values_maps_to.csv")

    df_relationships = pd.concat(
        [
            _fix_relationship_dates(_drop_index_columns(concept_relationship_staging)),
            _fix_relationship_dates(cs_values_maps_to_value),
            _fix_relationship_dates(_drop_index_columns(cs_values_maps_to)),
        ],
        ignore_index=True,
    )
    safe_insert("CONCEPT_RELATIONSHIP", df_relationships, engine)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: load the full DICOM vocabulary into the OMOP database."""
    parser = argparse.ArgumentParser(description="Load the DICOM vocabulary tables into OMOP.")
    parser.add_argument(
        "--vocab-dir",
        type=Path,
        default=DEFAULT_VOCAB_DIR,
        help="Directory holding the DICOM vocab CSVs (a sibling <dir>.zip is auto-extracted).",
    )
    args = parser.parse_args(argv)

    print("🩻 Loading DICOM vocabulary tables...")
    ensure_vocab_dir(args.vocab_dir)
    engine = create_engine(get_settings().OMOP_DATABASE_URL.get_secret_value(), echo=False)

    load_vocabulary_metadata(engine)
    load_concepts(engine, args.vocab_dir)
    load_relationships(engine, args.vocab_dir)
    print("DICOM vocabulary load complete!")


if __name__ == "__main__":
    main()
