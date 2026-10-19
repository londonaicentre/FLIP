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
"""Load a licence holder's converted ADNI biomarker cohort into a running trust OMOP database.

This is the populate side of the biomarker causal-discovery tutorial
(``fl-tutorials/{nvflare/tabular_causal_discovery,flower}/biomarker_causal_discovery``) on a **local**
dev stack. The tutorial's ``query.sql`` pivots ``omop.measurement`` rows matched on
``measurement_source_value`` (``PLASMA_PTAU217``, ``ICV_MM3``, …) plus an ``EDUCATION_YEARS``
observation into one row per person; the shipped mock OMOP carries none of those, so without this
loader the cohort query returns nothing.

The data is ADNI, which is access-controlled: nothing derived from it may be published, so unlike
the Synthea loader this one downloads nothing. It reads the twelve-column ``dataframe.csv`` that
``make -C fl-tutorials convert-adni-cohort`` built from the user's own IDA download (gitignored,
never committed) and appends a per-trust ``person_id % num_trusts`` slice into **that user's own**
trust databases — the same machines, the same team, which the Data Use Agreement allows. It must
never be pointed at a shared or published trust.

Design notes, mirroring ``synthea_ehr.py``:

* **Coexistence.** Ids are shifted by ``PERSON_ID_OFFSET`` so they never collide with the imaging
  cohorts (NHS-number-derived, bounded by 999_999_999) or the Synthea slice (1_000_000_000 + id).
  Idempotency deletes by **provenance** — only persons whose ``person_source_value`` carries this
  loader's ``adni-cohort-`` prefix, and their measurement/observation rows — never by id range.
* **FK safety.** Every ``*_concept_id`` the vocabulary would constrain is ``0`` ("No matching
  concept") except ``gender_concept_id`` (8507/8532, which ``query.sql`` reads for ``is_female``).
  The query matches on the source-value strings, so zeroed concepts cost it nothing.
* **Dates.** The cohort CSV carries age, not scan dates (ADNI publishes month/year of birth only).
  Every baseline measurement is dated ``BASELINE_DATE`` and ``year_of_birth`` is set so the
  query's ``scan year − year_of_birth`` returns the CSV's age exactly; ``birth_datetime`` is filled
  because data-access-api's age statistic reads it (a NULL 500s every cohort submission).
* **Per-trust split.** ``person_id % num_trusts == trust_index − 1`` — the same convention as the
  converter's ``site1/``/``site2/`` CSVs, so trust 1 holds exactly what the simulator's site 1 held.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd
from sqlalchemy import Connection, Engine, create_engine, text

from omop_db_tools.config import get_settings

#: The converter's column contract (datasets/ad_biomarkers/make_adni_cohort.py; query.sql returns the same).
COHORT_COLUMNS = [
    "person_id",
    "age",
    "is_female",
    "education_years",
    "apoe4_copies",
    "plasma_ab42_ab40_ratio",
    "plasma_ptau217",
    "plasma_nfl",
    "plasma_gfap",
    "icv_mm3",
    "hippocampus_mm3",
    "adas_cog13",
]
#: ``measurement_source_value`` codes query.sql matches on, per cohort column.
MEASUREMENT_CODES = {
    "apoe4_copies": "APOE4_COPIES",
    "plasma_ab42_ab40_ratio": "PLASMA_AB42_AB40_RATIO",
    "plasma_ptau217": "PLASMA_PTAU217",
    "plasma_nfl": "PLASMA_NFL",
    "plasma_gfap": "PLASMA_GFAP",
    "icv_mm3": "ICV_MM3",
    "hippocampus_mm3": "HIPPOCAMPUS_MM3",
    "adas_cog13": "ADAS_COG13",
}
EDUCATION_CODE = "EDUCATION_YEARS"

#: Clear of the imaging cohorts (≤ 999_999_999) and the Synthea slice (1_000_000_000 + id ≤ ~1_000_002_000);
#: well below the int32 ceiling.
PERSON_ID_OFFSET = 1_200_000_000
ADNI_SOURCE_VALUE_PREFIX = "adni-cohort-"
#: The nominal baseline date every measurement carries; year_of_birth = its year − age.
BASELINE_DATE = pd.Timestamp("2024-01-15")
_NO_MATCHING_CONCEPT_ID = 0
_GENDER_CONCEPT = {0: 8507, 1: 8532}  # OMOP standard MALE / FEMALE


def read_cohort(path: Path) -> pd.DataFrame:
    """Read the converter's ``dataframe.csv`` and check its column contract (names, not values)."""
    frame = pd.read_csv(path)
    missing = [c for c in COHORT_COLUMNS if c not in frame.columns]
    if missing:
        raise SystemExit(f"❌ {path} lacks the cohort columns {missing}; build it with convert-adni-cohort")
    return frame[COHORT_COLUMNS]


def _trust_slice(frame: pd.DataFrame, num_trusts: int, trust_index: int) -> pd.DataFrame:
    """This trust's ``person_id % num_trusts`` slice — the converter's ``site<trust_index>/`` rows."""
    return frame[frame["person_id"] % num_trusts == (trust_index - 1)].reset_index(drop=True)


def build_person_rows(cohort: pd.DataFrame) -> pd.DataFrame:
    """The OMOP ``person`` rows: id-shifted, provenance-marked, with the age-preserving birth year."""
    year_of_birth = BASELINE_DATE.year - cohort["age"].astype(int)
    return pd.DataFrame(
        {
            "person_id": cohort["person_id"].astype(int) + PERSON_ID_OFFSET,
            "gender_concept_id": cohort["is_female"].astype(int).map(_GENDER_CONCEPT).astype(int),
            "year_of_birth": year_of_birth,
            "birth_datetime": pd.to_datetime(year_of_birth.astype(str) + "-07-01"),
            "race_concept_id": _NO_MATCHING_CONCEPT_ID,
            "ethnicity_concept_id": _NO_MATCHING_CONCEPT_ID,
            "person_source_value": cohort["person_id"].astype(int).map(lambda pid: f"{ADNI_SOURCE_VALUE_PREFIX}{pid}"),
        }
    )


def build_measurement_rows(cohort: pd.DataFrame) -> pd.DataFrame:
    """One ``measurement`` row per person and code, dated ``BASELINE_DATE``."""
    parts = [
        pd.DataFrame(
            {
                "person_id": cohort["person_id"].astype(int) + PERSON_ID_OFFSET,
                "measurement_concept_id": _NO_MATCHING_CONCEPT_ID,
                "measurement_date": BASELINE_DATE.date(),
                "measurement_type_concept_id": _NO_MATCHING_CONCEPT_ID,
                "value_as_number": cohort[column].astype(float),
                "measurement_source_value": code,
            }
        )
        for column, code in MEASUREMENT_CODES.items()
    ]
    rows = pd.concat(parts, ignore_index=True)
    rows.insert(0, "measurement_id", rows.index.to_numpy() + 1 + PERSON_ID_OFFSET)
    return rows


def build_observation_rows(cohort: pd.DataFrame) -> pd.DataFrame:
    """The ``EDUCATION_YEARS`` observation, one per person."""
    rows = pd.DataFrame(
        {
            "person_id": cohort["person_id"].astype(int) + PERSON_ID_OFFSET,
            "observation_concept_id": _NO_MATCHING_CONCEPT_ID,
            "observation_date": BASELINE_DATE.date(),
            "observation_type_concept_id": _NO_MATCHING_CONCEPT_ID,
            "value_as_number": cohort["education_years"].astype(float),
            "observation_source_value": EDUCATION_CODE,
        }
    )
    rows.insert(0, "observation_id", rows.index.to_numpy() + 1 + PERSON_ID_OFFSET)
    return rows


def clean_by_provenance(conn: Connection) -> None:
    """Delete a previous load of this cohort — the persons this loader marked, and their rows.

    Runs on the caller's connection so the delete commits together with the inserts that replace
    it. Child tables first, so the delete is safe whether or not FK constraints have been applied.
    Never filters on an id range (see ``synthea_ehr.clean_reserved_band`` for why that destroyed
    imaging-cohort persons).
    """
    marker = f"{ADNI_SOURCE_VALUE_PREFIX}%"
    owned = "SELECT person_id FROM omop.person WHERE person_source_value LIKE :marker"
    conn.execute(text(f"DELETE FROM omop.measurement WHERE person_id IN ({owned})"), {"marker": marker})
    conn.execute(text(f"DELETE FROM omop.observation WHERE person_id IN ({owned})"), {"marker": marker})
    conn.execute(text("DELETE FROM omop.person WHERE person_source_value LIKE :marker"), {"marker": marker})


def load_adni_cohort(engine: Engine, frame: pd.DataFrame, num_trusts: int, trust_index: int) -> int:
    """Load this trust's slice of the cohort in one transaction. Returns the person count loaded."""
    cohort = _trust_slice(frame, num_trusts, trust_index)
    person_rows = build_person_rows(cohort)
    measurement_rows = build_measurement_rows(cohort)
    observation_rows = build_observation_rows(cohort)
    with engine.begin() as conn:
        clean_by_provenance(conn)
        person_rows.to_sql("person", conn, if_exists="append", index=False, schema="omop")
        measurement_rows.to_sql("measurement", conn, if_exists="append", index=False, schema="omop")
        observation_rows.to_sql("observation", conn, if_exists="append", index=False, schema="omop")
    print(
        f"✅ trust {trust_index}/{num_trusts}: loaded {len(person_rows)} persons, "
        f"{len(measurement_rows)} measurements, {len(observation_rows)} observations"
    )
    return len(person_rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cohort", type=Path, required=True, help="The converter's dataframe.csv (never committed).")
    parser.add_argument("--trust-index", type=int, required=True, help="1-based index of this trust.")
    parser.add_argument("--num-trusts", type=int, default=2, help="Total number of trusts (person_id split).")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Load into a database that is not on this host, or under PROD. Off by default: the cohort is "
        "licence-restricted and belongs on the licence holder's own machines only.",
    )
    return parser.parse_args(argv)


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def refuse_unless_local(host: str, env: dict, allow_remote: bool) -> None:
    """The documentation's 'never a shared or published trust' rule, enforced.

    A remote ``OMOP_DB_HOST`` or a ``PROD`` environment means a trust other people's projects read;
    ADNI rows there would breach the Data Use Agreement. ``--allow-remote`` is the explicit override.
    """
    if allow_remote:
        return
    problems = []
    if host not in LOCAL_HOSTS:
        problems.append(f"OMOP_DB_HOST={host!r} is not this host")
    if env.get("PROD"):
        problems.append(f"PROD={env['PROD']!r} selects a deployed environment")
    if problems:
        raise SystemExit(
            "❌ Refusing to load the ADNI cohort: " + "; ".join(problems) + ". This loader is for a LOCAL dev "
            "trust only (the Data Use Agreement covers the licence holder's own machines); pass --allow-remote "
            "only if that trust is yours alone."
        )


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: read the converted cohort and load this trust's slice into OMOP."""
    args = parse_args(argv)
    if args.num_trusts < 1 or not 1 <= args.trust_index <= args.num_trusts:
        raise SystemExit(f"❌ Need 1 <= --trust-index <= --num-trusts (got {args.trust_index}/{args.num_trusts})")
    if not args.cohort.exists():
        raise SystemExit(f"❌ {args.cohort} does not exist — run `make -C fl-tutorials convert-adni-cohort` first")
    frame = read_cohort(args.cohort)
    settings = get_settings()
    refuse_unless_local(settings.OMOP_DB_HOST, dict(os.environ), args.allow_remote)
    engine = create_engine(settings.OMOP_DATABASE_URL.get_secret_value(), echo=False)
    load_adni_cohort(engine, frame, args.num_trusts, args.trust_index)
    print("🎉 ADNI biomarker cohort loaded — the tutorial's query.sql now returns it on this trust.")


if __name__ == "__main__":
    main()
