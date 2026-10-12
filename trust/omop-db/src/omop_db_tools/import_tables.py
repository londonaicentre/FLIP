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
"""Populate one trust's OMOP database from the canonical mock dataset.

Reads the canonical <data-dir>/<project>/<table>.csv files (see dataset.py for
how they are built/fetched), extracts this trust's deterministic slice, and
appends it into the ``omop`` schema.

Two callers, one loader (FLIP#1100):

* The build-stack ``populate`` path (``--clean all``): a freshly-initialised
  database with no constraints, every mock-data table emptied first, then
  ``make apply-constraints``.
* The seed path (``--clean projects``, the default): a *running* trust database
  that already carries the vocabulary and the FK constraints, and possibly rows
  that are not ours to touch — Synthea EHR persons (FLIP#1068), other projects.
  Only the listed projects' own rows are removed, keyed by their ``person_id``s.

Both work on a constrained database because ``CANONICAL_TABLES`` is in FK-safe
order and each project is loaded inside one transaction: a failure part-way
leaves the database as it was, never half a project.
"""

import argparse
import re
from pathlib import Path

import pandas as pd
from sqlalchemy import Connection, Engine, create_engine, text

from omop_db_tools.config import get_settings
from omop_db_tools.dataset import (
    CANONICAL_TABLES,
    DEFAULT_PROJECTS,
    LEGACY_MODE_ALIAS,
    OPTIONAL_TABLES,
    PARTITION_MODES,
    split_for_trust,
)

CLEAN_MODES = ["projects", "all"]

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_identifier(name: str) -> str:
    """Allow a string into SQL as an identifier only if it is a plain SQL name.

    Args:
        name (str): Candidate table/column name.

    Returns:
        str: The validated name, unchanged.

    Raises:
        ValueError: If the name is not a bare [A-Za-z_][A-Za-z0-9_]* identifier.
    """
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


def validate_data_dir(data_dir: Path, projects: list[str]) -> None:
    """Fail before any destructive step when a required table CSV is missing.

    Args:
        data_dir (Path): Directory holding the canonical <project>/<table>.csv files.
        projects (list[str]): Project names that will be loaded.

    Raises:
        FileNotFoundError: If any required table CSV is absent — checked up
            front so clean_tables never wipes a database that cannot be refilled.
    """
    for project in projects:
        for table_name in CANONICAL_TABLES:
            csv_file_path = data_dir / project / f"{table_name}.csv"
            if not csv_file_path.is_file() and table_name not in OPTIONAL_TABLES:
                raise FileNotFoundError(f"Required table CSV not found: {csv_file_path}")


def clean_tables(engine: Engine) -> None:
    """Delete ALL rows from the mock-data tables — the build-stack ``--clean all`` path.

    Reverse insert order, which is FK-safe (see ``CANONICAL_TABLES``). Never run
    this against a running trust: it takes Synthea EHR rows and every other
    project with it. ``clean_project`` is the seed path's scoped equivalent.
    """
    with engine.begin() as conn:
        for table_name in reversed(CANONICAL_TABLES):
            print(f"🧹 Cleaning table: {table_name}")
            conn.execute(text(f"DELETE FROM omop.{validate_identifier(table_name)};"))
    print("✅ All target tables cleaned.\n")


def project_person_ids(data_dir: Path, project: str) -> list[int]:
    """Every ``person_id`` the canonical dataset assigns to ``project`` — across all trusts.

    This is the provenance key the seed path cleans by (FLIP#1100). The whole
    project, not this trust's slice, so a database that somehow holds another
    trust's rows for the project (a partition change, a mis-seeded kit) is put
    right by the next seed rather than accumulating. Deleting by ``person_id``
    rather than by a surrogate-key band mirrors ``synthea_ehr.clean_reserved_band``:
    every canonical table carries the column, whereas the per-project id blocks
    do not cover ``person`` or cxr's derived ``image_feature_id``s.

    Args:
        data_dir (Path): Directory holding the canonical <project>/<table>.csv files.
        project (str): Project name.

    Returns:
        list[int]: The project's person ids.
    """
    return pd.read_csv(data_dir / project / "person.csv", usecols=["person_id"])["person_id"].astype(int).tolist()


def clean_project(conn: Connection, person_ids: list[int]) -> None:
    """Delete one project's rows from every mock-data table, keyed by its persons.

    Runs inside the caller's transaction, in reverse insert order so it is
    FK-safe on a constrained database. Rows belonging to anyone else — Synthea
    persons, other projects — are untouched by construction.

    Args:
        conn (Connection): An open transaction on the target database.
        person_ids (list[int]): The project's ``person_id``s (see ``project_person_ids``).
    """
    for table_name in reversed(CANONICAL_TABLES):
        result = conn.execute(
            text(f"DELETE FROM omop.{validate_identifier(table_name)} WHERE person_id = ANY(:ids);"),
            {"ids": person_ids},
        )
        print(f"🧹 omop.{table_name}: removed {result.rowcount} existing row(s) for these persons")


def remove_projects(engine: Engine, data_dir: Path, projects: list[str]) -> None:
    """Delete the listed projects' rows, keyed by the ``person.csv`` in ``data_dir``, and load nothing.

    The unseed twin of ``load_project``'s scoped clean: point ``data_dir`` at the tables of the cut
    that IS in the database (the previous data-version tag) to take exactly those rows out — the
    move off a re-cut project whose person ids all changed, leaving every other project in place.
    One transaction for all listed projects.

    Args:
        engine (Engine): Target database.
        data_dir (Path): Directory holding the canonical <project>/person.csv files of the cut to remove.
        projects (list[str]): Project names.

    Raises:
        FileNotFoundError: If a project's ``person.csv`` is absent — checked before anything is deleted.
    """
    for project in projects:
        if not (data_dir / project / "person.csv").is_file():
            raise FileNotFoundError(f"Cannot unseed {project}: {data_dir / project / 'person.csv'} not found")
    with engine.begin() as conn:
        for project in projects:
            print(f"🧹 Removing {project} as {data_dir} describes it")
            clean_project(conn, project_person_ids(data_dir, project))
    print("✅ Unseeded: " + ", ".join(projects))


def load_project(
    engine: Engine,
    data_dir: Path,
    project: str,
    num_trusts: int,
    trust_index: int,
    partition: str,
    clean: str = "projects",
) -> None:
    """Load one project's tables, filtered to this trust's slice of the canonical dataset.

    One transaction per project: the scoped clean (when ``clean == "projects"``)
    and every table insert commit together or not at all.

    Args:
        engine (Engine): Target database.
        data_dir (Path): Directory holding the canonical <project>/<table>.csv files.
        project (str): Project name.
        num_trusts (int): Total number of trusts being stood up.
        trust_index (int): 1-based index of this trust.
        partition (str): A ``PARTITION_MODES`` entry (or the legacy alias).
        clean (str): ``"projects"`` removes this project's existing rows first;
            ``"all"`` assumes ``clean_tables`` already ran and skips the scoped clean.
    """
    print(f"📦 Loading data for trust {trust_index}/{num_trusts} / {project} (partition: {partition})")
    with engine.begin() as conn:
        if clean == "projects":
            clean_project(conn, project_person_ids(data_dir, project))
        for table_name in CANONICAL_TABLES:
            csv_file_path = data_dir / project / f"{table_name}.csv"
            if not csv_file_path.is_file():
                if table_name in OPTIONAL_TABLES:
                    print(f"⚠️  Optional table CSV not found, skipping: {csv_file_path}")
                    continue
                raise FileNotFoundError(f"Required table CSV not found: {csv_file_path}")

            full = pd.read_csv(csv_file_path)
            if full.empty and table_name not in OPTIONAL_TABLES:
                raise ValueError(f"{csv_file_path} contains headers but no rows — refusing to load a truncated table")
            df = split_for_trust(full, num_trusts, trust_index, partition)
            df.to_sql(table_name, conn, if_exists="append", index=False, schema="omop")
            print(f"✅ Inserted {len(df)} rows into omop.{table_name}")
    print(" ")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: clean (all, or just the listed projects), then load this trust's slice."""
    parser = argparse.ArgumentParser(description="Load this trust's slice of the canonical CSV dataset into OMOP.")
    parser.add_argument("--trust-index", type=int, required=True, help="1-based index of this trust.")
    parser.add_argument("--num-trusts", type=int, default=2, help="Total number of trusts being stood up.")
    parser.add_argument(
        "--partition",
        choices=PARTITION_MODES + [LEGACY_MODE_ALIAS],
        default="source_trust",
        help="Split mode: 'source_trust' partitions by the dataset's own column, which the per-project DICOM "
        "sets are keyed on too ('legacy' is an accepted alias); 'modulo' partitions person_id %% num-trusts "
        "for a dataset that carries no such column.",
    )
    parser.add_argument(
        "--clean",
        choices=CLEAN_MODES,
        default="projects",
        help="'projects' (default) removes only the listed projects' existing rows, by their person_ids, so a "
        "running trust keeps its Synthea EHR rows and other projects; 'all' empties every mock-data table "
        "first — the build-stack populate path, never a running trust.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/canonical"),
        help="Directory holding the canonical <project>/<table>.csv files.",
    )
    parser.add_argument(
        "--projects",
        nargs="+",
        default=DEFAULT_PROJECTS,
        help="Project names to load (default: %(default)s).",
    )
    parser.add_argument(
        "--remove-only",
        action="store_true",
        help="Unseed: delete the listed projects' rows (by the person_ids in --data-dir's person.csv, i.e. the "
        "cut those tables describe) and load nothing — how a re-cut project's previous rows leave a running "
        "trust without touching any other project.",
    )
    args = parser.parse_args(argv)

    engine = create_engine(get_settings().OMOP_DATABASE_URL.get_secret_value(), echo=False)

    if args.remove_only:
        remove_projects(engine, args.data_dir, args.projects)
        return

    validate_data_dir(args.data_dir, args.projects)
    if args.clean == "all":
        clean_tables(engine)
    for project in args.projects:
        load_project(
            engine, args.data_dir, project, args.num_trusts, args.trust_index, args.partition, clean=args.clean
        )

    with engine.begin() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) FROM omop.{validate_identifier(CANONICAL_TABLES[0])};"))
        print(f"\nTotal rows in omop.{CANONICAL_TABLES[0]} (sanity check): {result.scalar()}")

    print(f"\n🎉 Finished populating OMOP database for trust {args.trust_index}/{args.num_trusts}")


if __name__ == "__main__":
    main()
