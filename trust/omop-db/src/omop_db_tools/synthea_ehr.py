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
"""Load the public Synthea-in-OMOP tabular cohort into a running trust OMOP database.

This is the populate side of the EHR risk-prediction tutorial
(``fl-tutorials/{nvflare/tabular_classification,flower}/ehr_risk_prediction``). That tutorial's
``query.sql`` reads ``omop.person`` / ``omop.condition_occurrence`` / ``omop.visit_occurrence``,
but the shipped mock OMOP carries no condition rows at all — so without this loader the cohort
query returns nothing. Here we download three OMOP CDM tables of the fully synthetic 1k-person
Synthea dataset from the AWS Open Data Registry (https://registry.opendata.aws/synthea-omop/ —
anonymous HTTPS, no credentials, ~5 MB total, static since 2023) and append them into a
**running** trust database, the same seed-once model as ``load_core_vocab.sh``.

Design notes:

* **Coexistence with the imaging cohorts.** The mock OMOP already holds the imaging tutorials'
  persons (they carry visits but no conditions). Synthea ids are therefore shifted into a reserved
  high band (``PERSON_ID_OFFSET``) so they never collide with an existing primary key, and the
  tutorial's ``query.sql`` selects only persons that have a condition — i.e. exactly the rows
  loaded here. Re-running is idempotent: the reserved band is deleted first, then re-inserted.
* **FK safety.** FK constraints are absent from a freshly-initialised DB but present once
  ``load-omop-vocab`` has run. Every ``*_concept_id`` that would reference the vocabulary is set
  to ``0`` ("No matching concept", always present in a loaded vocab) **except** ``gender_concept_id``
  (kept as Synthea's standard 8507/8532, which ``query.sql`` reads for the ``is_female`` feature and
  which every OMOP vocabulary export carries). Conditions are matched on the ``condition_source_value``
  SNOMED string, so zeroing ``condition_concept_id`` costs the tutorial nothing.
* **Per-trust split.** Each trust is loaded with a disjoint ``person_id % num_trusts`` slice, so the
  federated run sees genuinely partitioned cohorts — the same modulo convention the mock trusts and
  the local-sim CSV builder use.
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine, text

from omop_db_tools.config import get_settings

# The 1k-person split of the Synthea-in-OMOP dataset on the AWS Open Data Registry (uncompressed
# CSVs; the larger synthea100k/synthea23m splits are LZO-compressed and far too big for a tutorial).
DEFAULT_BASE_URL = "https://synthea-omop.s3.amazonaws.com/synthea1k"
SOURCE_TABLES = ("person", "condition_occurrence", "visit_occurrence")

# Columns the derivation reads: upstream schema drift must fail loudly here, not surface as an
# empty cohort. condition_source_value is read as a string so SNOMED codes keep their exact form.
REQUIRED_SOURCE_COLUMNS = {
    "person": ["person_id", "gender_concept_id", "year_of_birth", "birth_datetime"],
    "condition_occurrence": ["person_id", "condition_source_value", "condition_start_date"],
    "visit_occurrence": ["person_id", "visit_start_date"],
}

# Shift Synthea ids into a reserved high band so they never collide with the imaging cohorts'
# existing primary keys. Must be large (Synthea 1k ids are <2000; imaging ids are far smaller) and
# even, so the person_id-modulo per-trust split is unchanged by the shift.
PERSON_ID_OFFSET = 900_000_000

# Standard OMOP Gender concepts, present in every vocabulary export — safe to keep even with FK
# constraints applied. query.sql reads gender_concept_id == 8532 for the is_female feature.
_STANDARD_GENDER_CONCEPT_IDS = (8507, 8532)
# "No matching concept": always present once a vocabulary is loaded, and query.sql never reads the
# concept_id columns we zero (it matches conditions on condition_source_value).
_NO_MATCHING_CONCEPT_ID = 0


def fetch_source_tables(base_url: str, cache_dir: Path) -> dict[str, pd.DataFrame]:
    """Download (or reuse cached) the raw Synthea OMOP tables and validate their schema.

    Args:
        base_url (str): Base URL of the Synthea-OMOP CSV tables.
        cache_dir (Path): Where the raw CSVs are cached (never committed).

    Returns:
        dict[str, pd.DataFrame]: The three source tables keyed by name.

    Raises:
        SystemExit: If a table cannot be downloaded or is missing an expected column.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    tables: dict[str, pd.DataFrame] = {}
    for table in SOURCE_TABLES:
        path = cache_dir / f"{table}.csv"
        if not path.exists():
            url = f"{base_url}/{table}.csv"
            print(f"⬇️  {url}")
            try:
                with urllib.request.urlopen(url, timeout=120) as response, open(path, "wb") as handle:
                    handle.write(response.read())
            except Exception as err:  # noqa: BLE001 - re-raised as a clear, actionable SystemExit
                path.unlink(missing_ok=True)
                raise SystemExit(
                    f"❌ Could not download {url}: {err}\n"
                    "   The AWS Open Data Synthea-OMOP layout may have changed — see "
                    "https://registry.opendata.aws/synthea-omop/"
                ) from err
        frame = pd.read_csv(path, dtype={"condition_source_value": str}, low_memory=False)
        missing = [column for column in REQUIRED_SOURCE_COLUMNS[table] if column not in frame.columns]
        if missing:
            raise SystemExit(f"❌ {path} is missing expected column(s) {missing} — upstream schema drift?")
        tables[table] = frame
    return tables


def _trust_person_ids(person: pd.DataFrame, num_trusts: int, trust_index: int) -> set[int]:
    """The raw Synthea person ids belonging to this trust's ``person_id % num_trusts`` slice."""
    mask = person["person_id"] % num_trusts == (trust_index - 1)
    return set(person.loc[mask, "person_id"].astype(int))


def build_person_rows(person: pd.DataFrame, person_ids: set[int]) -> pd.DataFrame:
    """Project the Synthea person table onto the OMOP columns, shifted into the reserved id band."""
    subset = person[person["person_id"].isin(person_ids)]
    gender = subset["gender_concept_id"].where(
        subset["gender_concept_id"].isin(_STANDARD_GENDER_CONCEPT_IDS), _NO_MATCHING_CONCEPT_ID
    )
    return pd.DataFrame(
        {
            "person_id": subset["person_id"].astype(int) + PERSON_ID_OFFSET,
            "gender_concept_id": gender.astype(int),
            "year_of_birth": subset["year_of_birth"].astype(int),
            # birth_datetime is nullable in the CDM but load-bearing for the platform:
            # data-access-api's age-distribution statistic computes from it, and a NULL
            # 500s every cohort submission for the tutorial.
            "birth_datetime": pd.to_datetime(subset["birth_datetime"]),
            "race_concept_id": _NO_MATCHING_CONCEPT_ID,
            "ethnicity_concept_id": _NO_MATCHING_CONCEPT_ID,
            "person_source_value": subset["person_id"].astype(int).map(lambda pid: f"synthea-{pid}"),
        }
    )


def build_condition_rows(condition: pd.DataFrame, person_ids: set[int]) -> pd.DataFrame:
    """Project the Synthea condition table onto the OMOP columns, keeping the SNOMED source value."""
    subset = condition[condition["person_id"].isin(person_ids)].reset_index(drop=True)
    return pd.DataFrame(
        {
            "condition_occurrence_id": subset.index.to_numpy() + 1 + PERSON_ID_OFFSET,
            "person_id": subset["person_id"].astype(int) + PERSON_ID_OFFSET,
            "condition_concept_id": _NO_MATCHING_CONCEPT_ID,
            "condition_start_date": pd.to_datetime(subset["condition_start_date"]).dt.date,
            "condition_type_concept_id": _NO_MATCHING_CONCEPT_ID,
            "condition_source_value": subset["condition_source_value"].astype("string"),
        }
    )


def build_visit_rows(visit: pd.DataFrame, person_ids: set[int]) -> pd.DataFrame:
    """Project the Synthea visit table onto the OMOP columns (end date falls back to start date)."""
    subset = visit[visit["person_id"].isin(person_ids)].reset_index(drop=True)
    start = pd.to_datetime(subset["visit_start_date"])
    end = pd.to_datetime(subset["visit_end_date"]) if "visit_end_date" in subset.columns else start
    return pd.DataFrame(
        {
            "visit_occurrence_id": subset.index.to_numpy() + 1 + PERSON_ID_OFFSET,
            "person_id": subset["person_id"].astype(int) + PERSON_ID_OFFSET,
            "visit_concept_id": _NO_MATCHING_CONCEPT_ID,
            "visit_start_date": start.dt.date,
            "visit_end_date": end.fillna(start).dt.date,
            "visit_type_concept_id": _NO_MATCHING_CONCEPT_ID,
        }
    )


def clean_reserved_band(engine: Engine) -> None:
    """Delete any previously loaded Synthea rows (the reserved id band) — makes reloads idempotent.

    Child tables are cleared before ``person`` so the delete is safe whether or not FK constraints
    have been applied (``load-omop-vocab``). Only the reserved band is touched, never imaging rows.
    """
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM omop.condition_occurrence WHERE condition_occurrence_id >= :offset"),
            {"offset": PERSON_ID_OFFSET},
        )
        conn.execute(
            text("DELETE FROM omop.visit_occurrence WHERE visit_occurrence_id >= :offset"),
            {"offset": PERSON_ID_OFFSET},
        )
        conn.execute(text("DELETE FROM omop.person WHERE person_id >= :offset"), {"offset": PERSON_ID_OFFSET})


def load_synthea_ehr(engine: Engine, tables: dict[str, pd.DataFrame], num_trusts: int, trust_index: int) -> int:
    """Load this trust's Synthea slice into the OMOP database. Returns the person count loaded."""
    person_ids = _trust_person_ids(tables["person"], num_trusts, trust_index)
    person_rows = build_person_rows(tables["person"], person_ids)
    condition_rows = build_condition_rows(tables["condition_occurrence"], person_ids)
    visit_rows = build_visit_rows(tables["visit_occurrence"], person_ids)

    clean_reserved_band(engine)
    # person first (parents), then the child tables — correct whether or not FK constraints exist.
    person_rows.to_sql("person", engine, if_exists="append", index=False, schema="omop")
    visit_rows.to_sql("visit_occurrence", engine, if_exists="append", index=False, schema="omop")
    condition_rows.to_sql("condition_occurrence", engine, if_exists="append", index=False, schema="omop")

    positives = int(condition_rows["condition_source_value"].eq("44054006").sum())
    print(
        f"✅ trust {trust_index}/{num_trusts}: loaded {len(person_rows)} persons, "
        f"{len(condition_rows)} conditions ({positives} type-2-diabetes), {len(visit_rows)} visits"
    )
    return len(person_rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trust-index", type=int, required=True, help="1-based index of this trust.")
    parser.add_argument("--num-trusts", type=int, default=2, help="Total number of trusts (person_id split).")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base URL of the Synthea-OMOP CSV tables.")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/synthea-ehr"),
        help="Where the raw Synthea OMOP CSVs are cached (never committed).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: fetch the Synthea tables and load this trust's slice into OMOP."""
    args = parse_args(argv)
    if args.num_trusts < 1 or not 1 <= args.trust_index <= args.num_trusts:
        raise SystemExit(f"❌ Need 1 <= --trust-index <= --num-trusts (got {args.trust_index}/{args.num_trusts})")
    tables = fetch_source_tables(args.base_url, args.cache_dir)
    engine = create_engine(get_settings().OMOP_DATABASE_URL.get_secret_value(), echo=False)
    load_synthea_ehr(engine, tables, args.num_trusts, args.trust_index)
    print("🎉 Synthea EHR cohort loaded — run the tutorial's query.sql to fetch it.")


if __name__ == "__main__":
    main()
