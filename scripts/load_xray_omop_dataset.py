#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "psycopg2-binary>=2.9",
#     "pydicom>=2.4",
#     "requests>=2.31",
# ]
# ///
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
"""Load a chest-X-ray site folder into a trust's OMOP database (and optionally Orthanc PACS).

This ingests the ``balanced_synthetic_split`` simulation dataset
(``flip-fl-xray-simulation``) into the MI-CDM OMOP schema used by FLIP trusts,
so that the shipped xray ``query.sql`` returns each loaded accession with its
per-pathology Yes/No labels, and so imaging-api can retrieve the matching DICOM
from PACS by accession number.

For every accession (one chest radiograph) the loader writes the standard
MI-CDM chain, mirroring the existing FLIP mock data:

    person -> visit_occurrence -> procedure_occurrence -> image_occurrence
                                                              |
                       (one pair per label column) image_feature -> observation

The label's Yes/No value is stored as ``observation.value_as_concept_id``
(Yes=4188539 / No=4188540) and surfaced by ``query.sql`` via the
image_feature -> observation pivot (``image_feature_event_field_concept_id =
1147304``, the "observation" table).

Source-of-truth for the per-accession labels is
``sample_get_dataframe_response.csv`` (which is precisely the expected output of
``query.sql``); laterality is read from ``sample_metadata.csv`` when present.

OMOP ``accession_id`` is set to the DICOM's own ``AccessionNumber`` tag — a
unique 12-digit value that is a conformant DICOM SH (<=16 chars), so imaging-api
(which queries PACS by AccessionNumber via XNAT DQR / C-FIND) can find the study
that OMOP references. The DICOM is uploaded to Orthanc unchanged. The long
folder-name id (which exceeds the 16-char DICOM AccessionNumber limit, so it
cannot itself be the accession number) is retained as the human-facing
``person_source_value`` for traceability. Each CSV label row is matched to its
DICOM via the folder name, and the DICOM's AccessionNumber becomes the key used
everywhere downstream.

The loader is idempotent: an accession already present in
``omop.image_occurrence`` is skipped (OMOP), and a study already present in
Orthanc under that AccessionNumber is skipped (PACS).

Usage (single folder):

    uv run scripts/load_xray_omop_dataset.py \
        --folder /data/.../balanced_synthetic_split/re_final_site1 \
        --db-host localhost --db-port 5434 --db-name trustomopdb \
        --db-user postgres --db-password some_password \
        --orthanc-url http://localhost:8042 --orthanc-user admin --orthanc-password admin \
        --trust-label Trust_1

Instead of passing connection flags individually, point ``--env-file`` at a trust
kit / ``.env`` file (e.g. ``.env.prod``) and the loader reads the connection
values from it:

    uv run scripts/load_xray_omop_dataset.py \
        --folder /data/.../balanced_synthetic_split/re_final_site1 \
        --env-file trust/.env.GSTT.production --trust-label Trust_1

The env keys consumed are ``OMOP_POSTGRES_USER`` / ``OMOP_POSTGRES_PASSWORD``
(must be write-capable — not the read-only ``DATA_ACCESS_*`` role) /
``OMOP_POSTGRES_DB`` / ``OMOP_DB_PORT`` for OMOP, and ``ORTHANC_USERNAME`` /
``ORTHANC_PASSWORD`` / ``PACS_UI_PORT`` for Orthanc (the URL is built as
``http://<--orthanc-host, default localhost>:<PACS_UI_PORT>``). Hosts are not
read from the env file — its service names are Docker-internal — so they default
to localhost; pass ``--db-host`` / ``--orthanc-host`` (or ``--orthanc-url``) for
any other topology. Any explicit flag overrides the corresponding env-file value.

Pass ``--folder`` more than once to load several folders into the same trust
(e.g. a site's train split + its holdoff split). Use ``--skip-orthanc`` to
populate OMOP only, ``--skip-omop`` to upload DICOMs only, ``--dry-run`` to
report what would happen without writing, and ``--limit N`` to process only the
first N accessions of each folder (handy for a quick smoke test).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

import psycopg2
import pydicom
import requests
from psycopg2.extras import execute_values

# ── OMOP concept ids ────────────────────────────────────────────────────────
# Resolved against the full OHDSI vocabulary shipped in the FLIP OMOP image.
# The first three labels are the ones the shipped xray query.sql pivots; the
# remaining four are stored so future/extended queries can read them too.
LABEL_CONCEPTS: dict[str, int] = {
    "Effusion": 4215818,  # SNOMED, Observation / Morph Abnormality
    "Edema": 4196943,  # SNOMED, Observation / Morph Abnormality
    "Consolidation": 4319884,  # SNOMED, Observation / Morph Abnormality
    "Infiltration": 4264333,  # SNOMED, Observation / Morph Abnormality
    "Lung Nodule or Mass": 4214641,  # SNOMED "Mass", Observation / Morph Abnormality
    "Pneumothorax": 253796,  # SNOMED, Condition / Disorder (no Observation-domain match)
    "Lungs in normal arrangement": 40481136,  # SNOMED, Observation / Clinical Finding
}

YES_CONCEPT_ID = 4188539  # SNOMED "Yes" (Meas Value)
NO_CONCEPT_ID = 4188540  # SNOMED "No"  (Meas Value)

VISIT_CONCEPT_ID = 9201  # Inpatient Visit
VISIT_TYPE_CONCEPT_ID = 32817  # EHR
PROCEDURE_CONCEPT_ID = 4163872  # Plain chest X-ray
PROCEDURE_TYPE_CONCEPT_ID = 32817  # EHR
MODALITY_CONCEPT_ID = 4056681  # Plain radiography
IMAGE_ANATOMY_CONCEPT_ID = 4213162  # Lung structure
FEATURE_TYPE_CONCEPT_ID = 32841  # EHR radiology report
OBS_TYPE_CONCEPT_ID = 32841  # EHR radiology report
EVENT_FIELD_OBSERVATION = 1147304  # CDM "observation" table (links image_feature -> observation)

GENDER_M_CONCEPT_ID = 8507
GENDER_F_CONCEPT_ID = 8532

LATERALITY_ANATOMY: dict[str, int] = {
    "left": 4195613,  # Left lung structure
    "right": 4141610,  # Right lung structure
}

DEFAULT_STUDY_DATE = "2026-01-01"  # fallback when a DICOM has no StudyDate


# ── Helpers ─────────────────────────────────────────────────────────────────


def _truthy(value: str | None) -> bool:
    """Interpret a dataframe label cell as a positive (Yes) value."""
    return str(value).strip().lower() in {"yes", "y", "true", "1", "positive"}


def _study_date_to_iso(raw: str | None) -> str:
    """DICOM ``YYYYMMDD`` -> ``YYYY-MM-DD``; falls back to a fixed date."""
    if raw and len(raw) == 8 and raw.isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return DEFAULT_STUDY_DATE


def _deterministic_demographics(accession_id: str) -> tuple[int, str, int]:
    """Derive a stable synthetic (gender_concept_id, gender_source_value, year_of_birth).

    The source dataset carries no demographics, so we hash the accession id to
    get a deterministic — but varied — gender and birth year. This lets cohort
    queries that filter on gender/age return non-degenerate cohorts. Values are
    clearly synthetic and reproducible across re-runs.
    """
    digest = hashlib.sha256(accession_id.encode()).digest()
    is_male = digest[0] % 2 == 0
    year_of_birth = 1940 + (digest[1] % 66)  # 1940..2005
    if is_male:
        return GENDER_M_CONCEPT_ID, "M", year_of_birth
    return GENDER_F_CONCEPT_ID, "F", year_of_birth


@dataclass
class Accession:
    """One chest-X-ray record assembled from the source CSVs + DICOM file."""

    accession_id: str  # the DICOM AccessionNumber tag — OMOP + PACS key
    source_name: str  # folder-name id (label-row key; kept as person_source_value)
    dcm_path: Path
    labels: dict[str, bool]  # label column -> positive?
    laterality: str | None  # "left" / "right" / None
    study_uid: str | None
    series_uid: str | None
    study_date: str


@dataclass
class Counters:
    """Per-run id allocators, seeded from each table's current max id."""

    person_id: int = 0
    visit_id: int = 0
    procedure_id: int = 0
    image_occurrence_id: int = 0
    image_feature_id: int = 0
    observation_id: int = 0

    def next(self, attr: str) -> int:
        value = getattr(self, attr) + 1
        setattr(self, attr, value)
        return value


@dataclass
class Stats:
    omop_inserted: int = 0
    omop_skipped: int = 0
    orthanc_uploaded: int = 0
    orthanc_skipped: int = 0
    missing_dcm: list[str] = field(default_factory=list)
    orthanc_failed: list[str] = field(default_factory=list)


# ── Source parsing ──────────────────────────────────────────────────────────


def read_folder(folder: Path, limit: int | None) -> list[Accession]:
    """Build the accession list for one site folder from its CSVs + DICOM tree."""
    df_csv = folder / "sample_get_dataframe_response.csv"
    if not df_csv.exists():
        raise FileNotFoundError(f"Missing dataframe CSV: {df_csv}")

    # Optional laterality lookup from the metadata/manifest.
    laterality_by_acc: dict[str, str] = {}
    for meta_name in ("sample_metadata.csv", "split_manifest.csv"):
        meta_csv = folder / meta_name
        if not meta_csv.exists():
            continue
        with meta_csv.open(newline="") as fh:
            for row in csv.DictReader(fh):
                acc = (row.get("accession_id") or "").strip()
                lat = (row.get("laterality") or "").strip().lower()
                if acc and lat in LATERALITY_ANATOMY:
                    laterality_by_acc[acc] = lat
        break

    resources = folder / "accession-resources"
    accessions: list[Accession] = []
    missing: list[str] = []
    with df_csv.open(newline="") as fh:
        reader = csv.DictReader(fh)
        label_cols = [c for c in (reader.fieldnames or []) if c in LABEL_CONCEPTS]
        unknown = [c for c in (reader.fieldnames or []) if c not in LABEL_CONCEPTS and c != "accession_id"]
        if unknown:
            print(f"  ! ignoring unmapped dataframe columns: {unknown}", file=sys.stderr)
        for row in reader:
            source_name = (row.get("accession_id") or "").strip()
            if not source_name:
                continue
            dcm_path = resources / source_name / f"{source_name}.dcm"
            if not dcm_path.exists():
                # No DICOM -> no AccessionNumber to key on; cannot load this row.
                missing.append(source_name)
                continue
            ds = pydicom.dcmread(str(dcm_path), stop_before_pixels=True)
            accession_id = str(getattr(ds, "AccessionNumber", "") or "").strip()
            if not accession_id:
                missing.append(source_name)
                continue
            accessions.append(
                Accession(
                    accession_id=accession_id,
                    source_name=source_name,
                    dcm_path=dcm_path,
                    labels={col: _truthy(row.get(col)) for col in label_cols},
                    laterality=laterality_by_acc.get(source_name),
                    study_uid=str(getattr(ds, "StudyInstanceUID", "") or "") or None,
                    series_uid=str(getattr(ds, "SeriesInstanceUID", "") or "") or None,
                    study_date=_study_date_to_iso(str(getattr(ds, "StudyDate", "") or "")),
                )
            )
            if limit is not None and len(accessions) >= limit:
                break
    if missing:
        print(
            f"  ! {len(missing)} row(s) had no usable DICOM/AccessionNumber (first 5: {missing[:5]})",
            file=sys.stderr,
        )
    return accessions


# ── OMOP loading ────────────────────────────────────────────────────────────


def seed_counters(cur) -> Counters:
    """Seed id allocators from the current max id of each target table."""

    def _max(table: str, col: str) -> int:
        cur.execute(f"SELECT COALESCE(MAX({col}), 0) FROM omop.{table}")
        return int(cur.fetchone()[0])

    return Counters(
        person_id=_max("person", "person_id"),
        visit_id=_max("visit_occurrence", "visit_occurrence_id"),
        procedure_id=_max("procedure_occurrence", "procedure_occurrence_id"),
        image_occurrence_id=_max("image_occurrence", "image_occurrence_id"),
        image_feature_id=_max("image_feature", "image_feature_id"),
        observation_id=_max("observation", "observation_id"),
    )


def existing_accessions(cur, candidates: list[str]) -> set[str]:
    """Return the subset of ``candidates`` already present in image_occurrence."""
    if not candidates:
        return set()
    cur.execute(
        "SELECT accession_id FROM omop.image_occurrence WHERE accession_id = ANY(%s)",
        (candidates,),
    )
    return {r[0] for r in cur.fetchall()}


def load_omop(cur, accs: list[Accession], counters: Counters, stats: Stats, dry_run: bool) -> None:
    """Insert the MI-CDM chain for each not-yet-present accession."""
    present = existing_accessions(cur, [a.accession_id for a in accs])

    persons: list[tuple] = []
    visits: list[tuple] = []
    procedures: list[tuple] = []
    images: list[tuple] = []
    observations: list[tuple] = []
    features: list[tuple] = []

    for acc in accs:
        if acc.accession_id in present:
            stats.omop_skipped += 1
            continue

        study_date = acc.study_date
        gender_cid, gender_src, yob = _deterministic_demographics(acc.accession_id)
        person_id = counters.next("person_id")
        visit_id = counters.next("visit_id")
        procedure_id = counters.next("procedure_id")
        image_id = counters.next("image_occurrence_id")

        # person_source_value is varchar(50); keep the human-facing folder-name id
        # for traceability (the conformant accession lives on image_occurrence).
        person_source = acc.source_name[:50]
        persons.append((person_id, gender_cid, yob, f"{yob}-01-01 00:00:00", 0, 0, person_source, gender_src))
        visits.append((visit_id, person_id, VISIT_CONCEPT_ID, study_date, study_date, VISIT_TYPE_CONCEPT_ID))
        procedures.append(
            (
                procedure_id,
                person_id,
                PROCEDURE_CONCEPT_ID,
                study_date,
                PROCEDURE_TYPE_CONCEPT_ID,
                1,
                visit_id,
                "Chest X-ray",
            )
        )
        images.append(
            (
                image_id,
                person_id,
                procedure_id,
                visit_id,
                IMAGE_ANATOMY_CONCEPT_ID,
                study_date,
                acc.study_uid,
                acc.series_uid,
                MODALITY_CONCEPT_ID,
                acc.accession_id,
            )
        )

        feature_anatomy = LATERALITY_ANATOMY.get(acc.laterality or "", IMAGE_ANATOMY_CONCEPT_ID)
        for col, positive in acc.labels.items():
            concept_id = LABEL_CONCEPTS[col]
            value_concept = YES_CONCEPT_ID if positive else NO_CONCEPT_ID
            observation_id = counters.next("observation_id")
            feature_id = counters.next("image_feature_id")
            observations.append(
                (observation_id, person_id, concept_id, study_date, OBS_TYPE_CONCEPT_ID, value_concept, visit_id, col)
            )
            features.append(
                (
                    feature_id,
                    person_id,
                    image_id,
                    EVENT_FIELD_OBSERVATION,
                    observation_id,
                    concept_id,
                    FEATURE_TYPE_CONCEPT_ID,
                    feature_anatomy,
                )
            )
        stats.omop_inserted += 1

    if dry_run or not persons:
        return

    execute_values(
        cur,
        "INSERT INTO omop.person "
        "(person_id, gender_concept_id, year_of_birth, birth_datetime, race_concept_id, "
        " ethnicity_concept_id, person_source_value, gender_source_value) VALUES %s",
        persons,
    )
    execute_values(
        cur,
        "INSERT INTO omop.visit_occurrence "
        "(visit_occurrence_id, person_id, visit_concept_id, visit_start_date, visit_end_date, "
        " visit_type_concept_id) VALUES %s",
        visits,
    )
    execute_values(
        cur,
        "INSERT INTO omop.procedure_occurrence "
        "(procedure_occurrence_id, person_id, procedure_concept_id, procedure_date, "
        " procedure_type_concept_id, quantity, visit_occurrence_id, procedure_source_value) VALUES %s",
        procedures,
    )
    execute_values(
        cur,
        "INSERT INTO omop.image_occurrence "
        "(image_occurrence_id, person_id, procedure_occurrence_id, visit_occurrence_id, "
        " anatomic_site_concept_id, image_occurrence_date, image_study_uid, image_series_uid, "
        " modality_concept_id, accession_id) VALUES %s",
        images,
    )
    execute_values(
        cur,
        "INSERT INTO omop.observation "
        "(observation_id, person_id, observation_concept_id, observation_date, "
        " observation_type_concept_id, value_as_concept_id, visit_occurrence_id, observation_source_value) VALUES %s",
        observations,
    )
    execute_values(
        cur,
        "INSERT INTO omop.image_feature "
        "(image_feature_id, person_id, image_occurrence_id, image_feature_event_field_concept_id, "
        " image_feature_event_id, image_feature_concept_id, image_feature_type_concept_id, "
        " anatomic_site_concept_id) VALUES %s",
        features,
    )


# ── Orthanc loading ─────────────────────────────────────────────────────────


def orthanc_has_accession(base: str, auth: tuple[str, str], accession_id: str) -> bool:
    resp = requests.post(
        f"{base}/tools/find",
        auth=auth,
        json={"Level": "Study", "Query": {"AccessionNumber": accession_id}},
        timeout=30,
    )
    resp.raise_for_status()
    return len(resp.json()) > 0


def upload_dicom(base: str, auth: tuple[str, str], acc: Accession) -> None:
    """Upload the DICOM to Orthanc unchanged (its AccessionNumber is already the OMOP key)."""
    resp = requests.post(
        f"{base}/instances",
        auth=auth,
        data=acc.dcm_path.read_bytes(),
        headers={"Content-Type": "application/dicom"},
        timeout=120,
    )
    resp.raise_for_status()


def load_orthanc(accs: list[Accession], base: str, auth: tuple[str, str], stats: Stats, dry_run: bool) -> None:
    base = base.rstrip("/")
    for acc in accs:
        try:
            if orthanc_has_accession(base, auth, acc.accession_id):
                stats.orthanc_skipped += 1
                continue
            if dry_run:
                stats.orthanc_uploaded += 1
                continue
            upload_dicom(base, auth, acc)
            stats.orthanc_uploaded += 1
        except Exception as exc:  # noqa: BLE001 — collect & report, keep going
            stats.orthanc_failed.append(f"{acc.accession_id}: {exc}")


# ── Environment-file support ──────────────────────────────────────────────────

# Maps a resolved connection setting to the env-file key it is read from — the
# names used by the trust kit files (trust/.env.<CODE>.<env>) and the hub .env.*
# files. OMOP_POSTGRES_* is the write-capable account (the loader INSERTs); the
# read-only DATA_ACCESS_* role cannot be used here.
_ENV_KEY_MAP: dict[str, str] = {
    "db_name": "OMOP_POSTGRES_DB",
    "db_port": "OMOP_DB_PORT",
    "db_user": "OMOP_POSTGRES_USER",
    "db_password": "OMOP_POSTGRES_PASSWORD",
    "orthanc_user": "ORTHANC_USERNAME",
    "orthanc_password": "ORTHANC_PASSWORD",
}

# Built-in fallbacks when neither a flag nor the env file supplies a value.
_DEFAULTS: dict[str, object] = {
    "db_host": "localhost",
    "db_port": 5432,
    "db_name": "trustomopdb",
    "db_user": "postgres",
    "db_password": "",
    "orthanc_host": "localhost",
    "orthanc_user": "admin",
    "orthanc_password": "admin",
}


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` env file (``#`` comments, blanks, optional ``export`` and quotes)."""
    env: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            env[key] = value
    return env


def resolve(name: str, cli_value, env: dict[str, str], cast=lambda x: x):
    """Resolve a setting with precedence: explicit CLI flag > env file > built-in default."""
    if cli_value is not None:
        return cli_value
    env_key = _ENV_KEY_MAP.get(name)
    if env_key and env.get(env_key):
        return cast(env[env_key])
    return _DEFAULTS.get(name)


# ── CLI ─────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--folder", action="append", required=True, type=Path, help="Site folder (repeatable).")
    p.add_argument("--trust-label", default="", help="Label for logging (e.g. Trust_1).")
    p.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Read connection values from a trust kit / .env file (OMOP_POSTGRES_USER, "
        "OMOP_POSTGRES_PASSWORD, OMOP_POSTGRES_DB, OMOP_DB_PORT, ORTHANC_USERNAME, "
        "ORTHANC_PASSWORD, PACS_UI_PORT). Explicit flags below override env-file values.",
    )
    # Connection flags default to None so an unset flag falls back to the env file
    # (if any), then to the built-in default in _DEFAULTS. Hosts are not read from
    # the env file (its service names are Docker-internal); they default to
    # localhost — pass the flag for any other topology.
    p.add_argument("--db-host", default=None, help="OMOP host (default: localhost).")
    p.add_argument("--db-port", type=int, default=None, help="OMOP port (env: OMOP_DB_PORT; default 5432).")
    p.add_argument("--db-name", default=None, help="OMOP database (env: OMOP_POSTGRES_DB).")
    p.add_argument("--db-user", default=None, help="OMOP user (env: OMOP_POSTGRES_USER; must be write-capable).")
    p.add_argument("--db-password", default=None, help="OMOP password (env: OMOP_POSTGRES_PASSWORD).")
    p.add_argument(
        "--orthanc-url",
        default=None,
        help="Orthanc base URL. If omitted, built from --orthanc-host + the env file's "
        "PACS_UI_PORT. Required (here or via env file) unless --skip-orthanc.",
    )
    p.add_argument("--orthanc-host", default=None, help="Host for the env-built Orthanc URL (default: localhost).")
    p.add_argument("--orthanc-user", default=None, help="Orthanc user (env: ORTHANC_USERNAME; default admin).")
    p.add_argument("--orthanc-password", default=None, help="Orthanc password (env: ORTHANC_PASSWORD; default admin).")
    p.add_argument("--skip-omop", action="store_true", help="Do not touch OMOP.")
    p.add_argument("--skip-orthanc", action="store_true", help="Do not touch Orthanc.")
    p.add_argument("--limit", type=int, default=None, help="Only first N accessions per folder (smoke test).")
    p.add_argument("--dry-run", action="store_true", help="Report actions without writing.")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.env_file is not None and not args.env_file.exists():
        print(f"ERROR: --env-file not found: {args.env_file}", file=sys.stderr)
        return 2
    env = parse_env_file(args.env_file) if args.env_file else {}

    # Effective connection settings: explicit flag > env file > built-in default.
    db_host = resolve("db_host", args.db_host, env)
    db_port = resolve("db_port", args.db_port, env, int)
    db_name = resolve("db_name", args.db_name, env)
    db_user = resolve("db_user", args.db_user, env)
    db_password = resolve("db_password", args.db_password, env)
    orthanc_user = resolve("orthanc_user", args.orthanc_user, env)
    orthanc_password = resolve("orthanc_password", args.orthanc_password, env)
    # Orthanc URL: explicit flag wins; otherwise build from PACS_UI_PORT in the env file.
    orthanc_url = args.orthanc_url
    if not orthanc_url and env.get("PACS_UI_PORT"):
        orthanc_host = resolve("orthanc_host", args.orthanc_host, env)
        orthanc_url = f"http://{orthanc_host}:{env['PACS_UI_PORT']}"

    if not args.skip_orthanc and not orthanc_url:
        print(
            "ERROR: an Orthanc URL is required unless --skip-orthanc is set "
            "(pass --orthanc-url, or supply PACS_UI_PORT via --env-file).",
            file=sys.stderr,
        )
        return 2

    folders: list[Path] = []
    for f in args.folder:
        if not f.exists():
            print(f"ERROR: folder not found: {f}", file=sys.stderr)
            return 2
        folders.append(f)

    tag = f"[{args.trust_label}] " if args.trust_label else ""
    mode = "DRY-RUN " if args.dry_run else ""
    print(f"{mode}{tag}loading {len(folders)} folder(s): {', '.join(p.name for p in folders)}")

    stats = Stats()
    conn = None
    try:
        if not args.skip_omop:
            conn = psycopg2.connect(
                host=db_host,
                port=db_port,
                dbname=db_name,
                user=db_user,
                password=db_password,
            )

        for folder in folders:
            accs = read_folder(folder, args.limit)
            print(f"{tag}{folder.name}: {len(accs)} accession(s) parsed")

            if not args.skip_omop and conn is not None:
                with conn.cursor() as cur:
                    counters = seed_counters(cur)
                    load_omop(cur, accs, counters, stats, args.dry_run)
                if args.dry_run:
                    conn.rollback()
                else:
                    conn.commit()

            if not args.skip_orthanc:
                load_orthanc(
                    accs,
                    orthanc_url,
                    (orthanc_user, orthanc_password),
                    stats,
                    args.dry_run,
                )
    finally:
        if conn is not None:
            conn.close()

    print("\n── Summary ──────────────────────────────────────────")
    print(f"  OMOP   : inserted={stats.omop_inserted}  skipped(existing)={stats.omop_skipped}")
    print(f"  Orthanc: uploaded={stats.orthanc_uploaded}  skipped(existing)={stats.orthanc_skipped}")
    if stats.missing_dcm:
        print(f"  ! missing DICOM files: {len(stats.missing_dcm)} (first 5: {stats.missing_dcm[:5]})")
    if stats.orthanc_failed:
        print(f"  ! Orthanc upload failures: {len(stats.orthanc_failed)} (first 3: {stats.orthanc_failed[:3]})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
