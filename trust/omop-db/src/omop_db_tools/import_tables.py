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

Run against a freshly-initialised database: FK constraints are intentionally
absent until ``make apply-constraints`` runs after the data load.
"""

import argparse
import re
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine, text

from omop_db_tools.config import get_settings
from omop_db_tools.dataset import CANONICAL_TABLES, DEFAULT_PROJECTS, OPTIONAL_TABLES, PARTITION_MODES, split_for_trust

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
    """Delete all rows from the mock-data tables.

    Order is the reverse of the insert order by convention only — populate
    targets constraint-free databases, so deletion order is not load-bearing.
    """
    with engine.begin() as conn:
        for table_name in reversed(CANONICAL_TABLES):
            print(f"🧹 Cleaning table: {table_name}")
            conn.execute(text(f"DELETE FROM omop.{validate_identifier(table_name)};"))
    print("✅ All target tables cleaned.\n")


def load_project(
    engine: Engine,
    data_dir: Path,
    project: str,
    num_trusts: int,
    trust_index: int,
    partition: str,
) -> None:
    """Load one project's tables, filtered to this trust's slice of the canonical dataset."""
    print(f"📦 Loading data for trust {trust_index}/{num_trusts} / {project} (partition: {partition})")
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
        df.to_sql(table_name, engine, if_exists="append", index=False, schema="omop")
        print(f"✅ Inserted {len(df)} rows into omop.{table_name}")
    print(" ")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: clean the mock-data tables, then load this trust's slice."""
    parser = argparse.ArgumentParser(description="Load this trust's slice of the canonical CSV dataset into OMOP.")
    parser.add_argument("--trust-index", type=int, required=True, help="1-based index of this trust.")
    parser.add_argument("--num-trusts", type=int, default=2, help="Total number of trusts being stood up.")
    parser.add_argument(
        "--partition",
        choices=PARTITION_MODES,
        default="legacy",
        help="Split mode: 'legacy' reproduces the original two-trust membership (consistent with the published "
        "mock PACS data); 'modulo' partitions person_id %% num-trusts for any trust count.",
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
    args = parser.parse_args(argv)

    validate_data_dir(args.data_dir, args.projects)
    engine = create_engine(get_settings().OMOP_DATABASE_URL.get_secret_value(), echo=False)

    clean_tables(engine)
    for project in args.projects:
        load_project(engine, args.data_dir, project, args.num_trusts, args.trust_index, args.partition)

    with engine.begin() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) FROM omop.{validate_identifier(CANONICAL_TABLES[0])};"))
        print(f"\nTotal rows in omop.{CANONICAL_TABLES[0]} (sanity check): {result.scalar()}")

    print(f"\n🎉 Finished populating OMOP database for trust {args.trust_index}/{args.num_trusts}")


if __name__ == "__main__":
    main()
