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
"""Generate the OMOP mock rows for the IDC digital-pathology project from the pinned manifest.

The tutorial's imaging comes from IDC on demand and is never re-hosted. What has to be *saved* for a
run to be reproducible is the much smaller thing: which slides were chosen, and the OMOP rows that
describe them. Those rows are derived here rather than published, so the repository carries a
manifest of a few hundred bytes per slide instead of gigabytes of DICOM.

Everything is a deterministic function of ``manifest.csv``: same manifest in, byte-identical CSVs out.
Nothing is randomised and no demographics are invented -- TCGA pathology DICOM is de-identified of
sex and age (every ``PatientSex`` in this collection is empty), so those columns carry OMOP's
"No matching concept" (0) rather than a fabricated value that downstream analysis might believe.

Output follows the seed pipeline's per-project layout, carrying the ``source_trust`` column the
partitioning is driven by::

    <out>/pathology_project/{person,visit_occurrence,procedure_occurrence,image_occurrence}.csv

Usage::

    python build_omop_project.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

logger = logging.getLogger("build_omop_project")

PROJECT_NAME = "pathology_project"
SOURCE_TRUST_COLUMN = "source_trust"

# Concept ids, all resolved against the vocabulary the dev OMOP database already loads. Notably the
# modality concept needs nothing added: the DICOM vocabulary already carries Slide microscopy.
MODALITY_SLIDE_MICROSCOPY = 2128009266  # DICOM 'SM', Slide microscopy
ANATOMIC_SITE_BREAST = 4298444  # SNOMED, Breast structure
PROCEDURE_BIOPSY_OF_BREAST = 4047494  # SNOMED, Biopsy of breast
PROCEDURE_TYPE_EHR = 32817  # matches the existing cxr/spleen projects
VISIT_INPATIENT = 9201  # matches the existing cxr/spleen projects
NO_MATCHING_CONCEPT = 0

# person.year_of_birth is NOT NULL, but TCGA pathology DICOM carries no birth date -- every
# PatientBirthDate in this collection is empty. 1900 is therefore a sentinel, not an estimate: it is
# chosen precisely because it is implausible, so a reader who sees it in an analysis knows the value
# is absent rather than quietly believing a realistic-looking year.
UNKNOWN_YEAR_OF_BIRTH = 1900

# The cohort-query discriminator. The other projects filter on procedure_source_value in exactly this
# way (for example 'Chest X-ray'), so query.sql can select this cohort without touching concept ids.
PROCEDURE_SOURCE_VALUE = "Histopathology slide"

# Existing projects occupy 1000002-3000900 across person/visit/procedure/image_occurrence, so this
# project is given its own band. Keeping the bands disjoint means several projects can be loaded into
# one trust without primary-key collisions.
ID_BASE = 4_000_000


def _site_to_source_trust(site: str) -> int:
    """Map a manifest site label (``Trust_1``) to the integer the seed pipeline partitions on."""
    digits = "".join(character for character in str(site) if character.isdigit())
    if not digits:
        raise ValueError(f"Cannot derive a source_trust from site {site!r}; expected a name like 'Trust_1'.")
    return int(digits)


def _study_date(value: object) -> str:
    """Normalise a StudyDate to an ISO date, accepting either form it arrives in.

    The raw DICOM tag is ``YYYYMMDD`` but the IDC index hands it back already normalised to
    ``YYYY-MM-DD``, so both are accepted rather than assuming whichever source was read last.

    These are conversion dates recorded by IDC, not dates of care -- the collection is de-identified.
    They are used rather than synthesised because a real value, however mundane, is auditable.
    """
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    raise ValueError(f"Unexpected StudyDate {value!r}; expected YYYYMMDD or YYYY-MM-DD.")


def build_tables(manifest: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build the four canonical OMOP tables for this project.

    Args:
        manifest: The pinned selection, one row per slide.

    Returns:
        A mapping of table name to dataframe, each carrying ``source_trust``.
    """
    rows = manifest.reset_index(drop=True)
    # Row position drives every surrogate key, and the manifest's own ordering is deterministic, so
    # regenerating from the same manifest reproduces the same ids.
    identifiers = rows.index + ID_BASE + 2
    source_trust = rows["site"].map(_site_to_source_trust)
    dates = rows["study_date"].map(_study_date)

    person = pd.DataFrame(
        {
            "person_id": identifiers,
            "gender_concept_id": NO_MATCHING_CONCEPT,
            "year_of_birth": UNKNOWN_YEAR_OF_BIRTH,
            "race_concept_id": NO_MATCHING_CONCEPT,
            "ethnicity_concept_id": NO_MATCHING_CONCEPT,
            "person_source_value": rows["patient_id"],
            SOURCE_TRUST_COLUMN: source_trust,
        }
    )
    visit = pd.DataFrame(
        {
            "visit_occurrence_id": identifiers,
            "person_id": identifiers,
            "visit_concept_id": VISIT_INPATIENT,
            "visit_start_date": dates,
            "visit_end_date": dates,
            "visit_type_concept_id": PROCEDURE_TYPE_EHR,
            SOURCE_TRUST_COLUMN: source_trust,
        }
    )
    procedure = pd.DataFrame(
        {
            "procedure_occurrence_id": identifiers,
            "person_id": identifiers,
            "procedure_concept_id": PROCEDURE_BIOPSY_OF_BREAST,
            "procedure_date": dates,
            "procedure_type_concept_id": PROCEDURE_TYPE_EHR,
            "visit_occurrence_id": identifiers,
            "procedure_source_value": PROCEDURE_SOURCE_VALUE,
            SOURCE_TRUST_COLUMN: source_trust,
        }
    )
    image_occurrence = pd.DataFrame(
        {
            "image_occurrence_id": identifiers,
            "person_id": identifiers,
            "procedure_occurrence_id": identifiers,
            "visit_occurrence_id": identifiers,
            "anatomic_site_concept_id": ANATOMIC_SITE_BREAST,
            "image_occurrence_date": dates,
            "image_study_uid": rows["slide_study_uid"],
            "image_series_uid": rows["slide_series_uid"],
            "modality_concept_id": MODALITY_SLIDE_MICROSCOPY,
            # The accession is the TCGA barcode, matching the DICOM AccessionNumber these slides
            # carry -- so a real-stack pull resolves on the same key the dev layout uses.
            "accession_id": rows["accession_id"],
            SOURCE_TRUST_COLUMN: source_trust,
        }
    )
    return {
        "person": person,
        "visit_occurrence": visit,
        "procedure_occurrence": procedure,
        "image_occurrence": image_occurrence,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parent / "manifest.csv",
        help="The pinned slide selection to derive rows from.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "omop",
        help="Directory to write <project>/<table>.csv into. Committed, so runs stay reproducible.",
    )
    args = parser.parse_args(argv)

    if not args.manifest.exists():
        raise SystemExit(
            f"No manifest at {args.manifest}. Generate one first with:\n"
            "  make -C fl-tutorials download-idc-pathology-data"
        )

    manifest = pd.read_csv(args.manifest, dtype={"tss": str, "study_date": str})
    tables = build_tables(manifest)

    destination = args.out / PROJECT_NAME
    destination.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        path = destination / f"{name}.csv"
        frame.to_csv(path, index=False)
        logger.info("Wrote %s (%d row(s))", path, len(frame))

    counts = manifest["site"].value_counts().to_dict()
    logger.info("Project %r covers %d patient(s) across %s", PROJECT_NAME, len(manifest), counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
