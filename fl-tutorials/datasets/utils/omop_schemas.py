# Copyright (c) 2023, NVIDIA CORPORATION.  All rights reserved.
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

"""Generate Pandera schemas for OMOP CDM 5.4 tables from the official PostgreSQL DDL.

Usage:
    from omop_schemas import schemas

Returns:
    schemas: dict[str, pa.DataFrameSchema]
        A dictionary mapping table names to their corresponding Pandera DataFrameSchema.

Notes:
    - The DDL is downloaded from the OHDSI GitHub repository.
    - SQL types are mapped to Pandera / pandas dtypes.
    - Columns are marked as nullable or not based on the DDL constraints.
    - The `required` attribute is assumed to be the opposite of `nullable`.
    - Primary keys are identified from the primary keys DDL file and marked as unique and required.
    - Missing columns are NOT added, because there is not safe default for nullable integer columns.
    - Schemas are cached alongside this module as committed YAML (see `SCHEMA_DIR`); importing this
      module never needs the network. Regenerate the cache with `python omop_schemas.py --regenerate`.
    - If run as a script, debug messages will be printed.

Imported from ``londonaicentre/flip-omop-mock-data`` (FLIP#1092) so the provenance of FLIP's
published mock OMOP data lives with the platform. The schemas produced are unchanged from that
repo; only the cache resolution (module-relative instead of cwd-relative) and the removal of the
import-time network fallback have changed, so an offline, cwd-independent test suite can import
this module.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandera.pandas as pa
import requests
import sqlglot
from pandera.io.pandas_io import from_yaml, to_yaml
from sqlglot import exp

# Configuration
DDL_URL = "https://raw.githubusercontent.com/OHDSI/CommonDataModel/v5.4.0/inst/ddl/5.4/postgresql/OMOPCDM_postgresql_5.4_ddl.sql"
PK_URL = DDL_URL.replace("_ddl.sql", "_primary_keys.sql")
SQL_DIALECT = "postgres"
_PK_RE = re.compile(
    r"""
    ALTER\ TABLE\s+@cdmDatabaseSchema\.(\w+)\s+
    ADD\s+CONSTRAINT\s+\w+\s+
    PRIMARY\s+KEY\s*\(([^)]+)\)
    """,
    re.IGNORECASE | re.VERBOSE,
)
"""This assumes a very regular format to the OHDSI primary key definitions, true in 5.4, e.g.:
```sql
ALTER TABLE @cdmDatabaseSchema.PERSON ADD CONSTRAINT xpk_PERSON PRIMARY KEY (person_id);
--etc.
```
"""


# Upstream toggled this from an `if __name__ == "__main__":` block; that block is now the
# --regenerate entry point below, so this is a plain default. Debug printing is a developer aid
# for --regenerate and has no effect on the schemas produced.
debug = False


def _debug_print(*args, **kwargs):
    """Print debug messages if debugging is enabled."""
    if debug:
        print(*args, **kwargs)


def _download_url(url: str) -> str:
    """Download text from the given URL."""
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.text


def _load_primary_keys(sql_text: str) -> dict[str, set[str]]:
    """Parse OMOP primary key definitions from SQL text.
    Uses regex rather than sqlglot because the OHDSI format is highly regular.

    Parameters:
        sql_text: str
            SQL text containing primary key definitions.

    Returns:
        dict[str, set[str]]
            Mapping of table names to sets of primary key column names."""
    pk_map: dict[str, set[str]] = {}

    for match in _PK_RE.finditer(sql_text):
        table = match.group(1).lower()
        cols = {c.strip().lower() for c in match.group(2).split(",")}
        pk_map.setdefault(table, set()).update(cols)

    return pk_map


def _sql_type_to_pandera(sql_type: str):
    """Map PostgreSQL types used in OMOP to Pandera / pandas dtypes."""
    sql_type = sql_type.lower()
    if sql_type.startswith(("integer", "int4", "int", "bigint", "int8", "smallint")):
        return int
    if sql_type.startswith(("varchar", "character varying", "text")):
        return str
    if sql_type in ("date", "timestamp", "timestamp without time zone"):
        return "datetime64[ns]"
    if sql_type.startswith(("decimal", "numeric")):
        return float
    # Fallback: treat unknown types as string
    _debug_print(f"Warning: Unsupported SQL type '{sql_type}'. Treating as string.")
    return str
    # raise NotImplementedError(f"Unsupported SQL type: {sql_type}")


def _column_is_nullable(column_def: exp.ColumnDef) -> bool:
    """Check if column definition allows NULL values.
    Raises an error if the column has no constraints or the expected NotNullColumnConstraint is missing.
    Returns True if column allows NULL, False if NOT NULL.
    """
    constraints = column_def.args.get("constraints")
    if not constraints:
        raise ValueError(f"Column '{column_def.name}' has no constraints")

    for c in constraints:
        if isinstance(c, exp.ColumnConstraint) and isinstance(c.kind, exp.NotNullColumnConstraint):
            # allow_null True -> nullable, False or absent -> not nullable
            return bool(c.kind.args.get("allow_null"))

    # If we reach here, no NotNullColumnConstraint found
    raise ValueError(f"Column '{column_def.name}' constraints do not include NotNullColumnConstraint")


def _extract_tables(ddl_sql: str, pk_sql: str) -> dict[str, dict[str, pa.Column]]:
    """Parse CREATE TABLE statements from the DDL SQL into sets of pandera columns."""
    primary_keys = _load_primary_keys(pk_sql)
    statements = sqlglot.parse(ddl_sql, read=SQL_DIALECT)

    tables: dict[str, dict[str, pa.Column]] = {}

    for stmt in statements:
        if not isinstance(stmt, exp.Create):
            _debug_print("Not a CREATE statement. Skipping.")
            continue

        if not isinstance(stmt.this, exp.Schema):
            _debug_print("CREATE statement does not include a schema. Skipping.")
            continue
        if not isinstance(stmt.this.this, exp.Table):
            _debug_print("CREATE statement does not create a table. Skipping.")
            continue
        table_name = stmt.this.this.name.lower()
        _debug_print(f"\nParsing table {table_name}:")

        columns: dict[str, pa.Column] = {}

        for element in stmt.this.expressions:
            if not isinstance(element, exp.ColumnDef):
                _debug_print("  Not a column definition. Skipping.")
                continue

            col_name = element.name.lower()
            _debug_print(f"  Found column {col_name}", end="")

            sql_type = element.args["kind"].sql(dialect=SQL_DIALECT)
            nullable = _column_is_nullable(element)
            is_pk = col_name in primary_keys.get(table_name, set())

            pa_dtype = _sql_type_to_pandera(sql_type)
            pa_type_name = pa_dtype if isinstance(pa_dtype, str) else pa_dtype.__name__
            _debug_print(
                f" ({sql_type}->{pa_type_name}, {'nullable' if nullable else 'not nullable'}{', PK' if is_pk else ''})"
            )
            columns[col_name] = pa.Column(pa_dtype, nullable=nullable, required=(is_pk or not nullable), unique=is_pk)

        tables[table_name] = columns

    return tables


def _build_schemas() -> dict[str, pa.DataFrameSchema]:
    """Build pandera schemas from table and column definitions."""
    pk_sql = _download_url(PK_URL)
    ddl_sql = _download_url(DDL_URL)
    table_columns = _extract_tables(ddl_sql, pk_sql)

    schemas: dict[str, pa.DataFrameSchema] = {}

    for table_name, columns in table_columns.items():
        schemas[table_name] = pa.DataFrameSchema(
            columns,
            strict=True,  # forbid extra columns
        )
    # Note: `coerce=True` was removed because pandera will raise an error if int columns are nullable,
    # because pandas int does not support null. We could still coerce individual columns if needed.
    # https://pandera.readthedocs.io/en/stable/dataframe_schemas.html#coercing-types-on-columns

    # Build MI-CDM tables manually
    schemas["image_occurrence"] = pa.DataFrameSchema(
        {
            "image_occurrence_id": pa.Column(int, nullable=False, required=True, unique=True),
            "person_id": pa.Column(int, nullable=False, required=True),
            "procedure_occurrence_id": pa.Column(int, nullable=False, required=True),
            "visit_occurrence_id": pa.Column(int, nullable=True, required=False, default=0),
            "anatomic_site_concept_id": pa.Column(int, nullable=True, required=False, default=0),
            "wadors_uri": pa.Column(str, nullable=True, required=False),
            "local_path": pa.Column(str, nullable=True, required=False),
            "image_occurrence_date": pa.Column("datetime64[ns]", nullable=False, required=True),
            "image_study_uid": pa.Column(str, nullable=False, required=True),
            "image_series_uid": pa.Column(str, nullable=False, required=True),
            "modality_concept_id": pa.Column(int, nullable=False, required=True),
            "accession_id": pa.Column(
                str, nullable=False, required=True
            ),  # this is not official and should be removed later
        },
        strict=True,
    )
    schemas["image_feature"] = pa.DataFrameSchema(
        {
            "image_feature_id": pa.Column(int, nullable=False, required=True, unique=True),
            "person_id": pa.Column(int, nullable=False, required=True),
            "image_occurrence_id": pa.Column(int, nullable=False, required=True),
            "image_feature_event_field_concept_id": pa.Column(int, nullable=True, required=False, default=0),
            "image_feature_event_id": pa.Column(int, nullable=True, required=False, default=0),
            "image_feature_concept_id": pa.Column(int, nullable=False, required=True),
            "image_feature_type_concept_id": pa.Column(int, nullable=False, required=True),
            "image_finding_concept_id": pa.Column(int, nullable=True, required=False, default=0),
            "image_finding_id": pa.Column(int, nullable=True, required=False, default=0),
            "anatomic_site_concept_id": pa.Column(int, nullable=True, required=False, default=0),
            "alg_system": pa.Column(str, nullable=True, required=False),
            "alg_datetime": pa.Column("datetime64[ns]", nullable=True, required=False),
        },
        strict=True,
    )

    return schemas


def _check_schemas(my_schemas: dict[str, pa.DataFrameSchema]) -> dict[str, pa.DataFrameSchema]:
    """Check that schemas dict contains the table names used to convert DICOM to OMOP."""

    required_tables = [
        "person",
        "visit_occurrence",
        "procedure_occurrence",
        "condition_occurrence",
        "observation",
        "measurement",
        "image_occurrence",
        "image_feature",
    ]
    for table in required_tables:
        if table not in my_schemas:
            raise ValueError(f"Schema for required table '{table}' is missing.")

    return my_schemas


# Derived schemas are cached beside this module and committed, so importing it never needs the
# network. Regenerate with `python omop_schemas.py --regenerate` after bumping DDL_URL.
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def build_and_cache_schemas() -> None:
    """Download the OMOP CDM DDL, derive the Pandera schemas and write them to SCHEMA_DIR."""
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for table_name, schema in _build_schemas().items():
        to_yaml(schema, SCHEMA_DIR / f"{table_name}.yaml")


def _load_cached_schemas() -> dict[str, pa.DataFrameSchema]:
    """Load the committed schema cache.

    Raises:
        FileNotFoundError: If the cache is missing, rather than silently reaching the network.
    """
    if not SCHEMA_DIR.is_dir():
        raise FileNotFoundError(
            f"{SCHEMA_DIR} is missing. Run `python {Path(__file__).name} --regenerate` to rebuild it "
            "(needs network access to fetch the OMOP CDM DDL)."
        )
    return {path.stem: from_yaml(path) for path in sorted(SCHEMA_DIR.glob("*.yaml"))}


__all__ = ["SCHEMA_DIR", "build_and_cache_schemas", "schemas"]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Regenerate the cached OMOP Pandera schemas")
    parser.add_argument("--regenerate", action="store_true", required=True, help="Rebuild SCHEMA_DIR from the DDL")
    parser.parse_args()
    build_and_cache_schemas()
    print(f"Wrote schemas to {SCHEMA_DIR}")
    raise SystemExit(0)

# Import path only: the script path above has already exited.
schemas = _check_schemas(_load_cached_schemas())
