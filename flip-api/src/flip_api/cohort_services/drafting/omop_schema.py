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

"""The OMOP tables a draft may use, and a sqlglot check that every table, column and literal comparison resolves.

``assets/omop_schema.json`` is generated from the trust's DDL, which is not in the flip-api image:

    uv run python -m flip_api.cohort_services.drafting.omop_schema ../trust/omop-db/files/OMOPCDM_postgresql_5.4_ddl.sql

``tests/unit/cohort_services/drafting/test_omop_schema.py`` fails when the copy drifts from the DDL.
"""

import json
import re
import sys
from functools import cache
from pathlib import Path

from sqlglot import exp
from sqlglot.errors import OptimizeError
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.optimizer.qualify import qualify

SCHEMA_PATH = Path(__file__).parent / "assets" / "omop_schema.json"

# The tables the prompt describes; anything else is rejected as unknown.
TABLES = (
    "person",
    "observation_period",
    "visit_occurrence",
    "condition_occurrence",
    "condition_era",
    "drug_exposure",
    "drug_era",
    "procedure_occurrence",
    "measurement",
    "observation",
    "death",
    "image_occurrence",
    "image_feature",
    "concept",
    "concept_ancestor",
)


def parse_ddl(text: str) -> dict[str, dict[str, str]]:
    """Read ``{table: {column: type}}`` for :data:`TABLES` from the OMOP CDM DDL.

    Args:
        text (str): The DDL file's contents.

    Returns:
        dict[str, dict[str, str]]: Column types per table, in DDL order.
    """
    schema: dict[str, dict[str, str]] = {}
    for table, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\);", text, re.S):
        cols = schema.setdefault(table.lower(), {})
        for line in body.split(","):
            m = re.match(r"\s*(\w+)\s+(\w+(?:\(\d+\))?)", line)
            if m:
                cols[m[1].lower()] = "int" if m[2].upper() == "SERIAL" else m[2].lower()
    for table, col, typ in re.findall(r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+) (\w+(?:\(\d+\))?)", text):
        schema[table.lower()][col.lower()] = typ.lower()
    return {t: schema[t] for t in TABLES}


@cache
def load_schema() -> dict[str, dict[str, str]]:
    """The shipped schema copy."""
    return json.loads(SCHEMA_PATH.read_text())


def summary(schema: dict[str, dict[str, str]]) -> str:
    """One line per table: ``name(col type, ...)``."""
    return "\n".join(f"{t}({', '.join(f'{c} {ty}' for c, ty in cols.items())})" for t, cols in schema.items())


def _is_number(t: exp.DataType) -> bool:
    return t.is_type(*exp.DataType.NUMERIC_TYPES)


def check(statement: exp.Expr, schema: dict[str, dict[str, str]]) -> str | None:
    """Check that every ``omop`` table and column exists and literal comparisons are type-sane.

    Args:
        statement (exp.Expr): A parsed SELECT.
        schema (dict[str, dict[str, str]]): Column types per table.

    Returns:
        str | None: None when the statement resolves, else a message the model can act on.
    """
    unknown = sorted({t.name for t in statement.find_all(exp.Table) if t.db == "omop" and t.name.lower() not in schema})
    if unknown:
        return f"Unknown OMOP table(s): {', '.join(unknown)}."
    try:
        tree = qualify(
            statement.copy(),
            schema={"omop": schema},
            dialect="postgres",
            validate_qualify_columns=True,
            quote_identifiers=False,
        )
    except OptimizeError as e:
        return f"Schema check: {e}. Use only columns that exist in the OMOP CDM 5.4 DDL."
    tree = annotate_types(tree, schema={"omop": schema}, dialect="postgres")
    # A string literal compared with a numeric column (or the reverse) is a Postgres type error at run time.
    for cmp in tree.find_all(exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.In):
        col = cmp.this if isinstance(cmp.this, exp.Column) else None
        if col is None or col.type is None or col.type.is_type(exp.DataType.Type.UNKNOWN):
            continue
        for lit in cmp.expressions if isinstance(cmp, exp.In) else [cmp.expression]:
            if not isinstance(lit, exp.Literal):
                continue
            if (lit.is_string and _is_number(col.type)) or (
                not lit.is_string and col.type.is_type(*exp.DataType.TEXT_TYPES)
            ):
                return (
                    f"Type mismatch: {col.sql('postgres')} is {col.type.sql('postgres')} but is compared with "
                    f"{lit.sql('postgres')}."
                )
    return None


if __name__ == "__main__":
    SCHEMA_PATH.write_text(json.dumps(parse_ddl(Path(sys.argv[1]).read_text()), indent=2) + "\n")
