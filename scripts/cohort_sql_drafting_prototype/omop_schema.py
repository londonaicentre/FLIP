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
"""OMOP CDM DDL -> {table: {column: type}}, plus a sqlglot check of every table/column and literal comparison."""

import re
from pathlib import Path

try:  # load() also serves helper.py, which runs without sqlglot
    from sqlglot import exp
    from sqlglot.errors import OptimizeError
    from sqlglot.optimizer.annotate_types import annotate_types
    from sqlglot.optimizer.qualify import qualify
except ImportError:
    pass


def load(ddl_path: Path) -> dict[str, dict[str, str]]:
    text = ddl_path.read_text()
    schema: dict[str, dict[str, str]] = {}
    for table, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\);", text, re.S):
        cols = schema.setdefault(table.lower(), {})
        for line in body.split(","):
            m = re.match(r"\s*(\w+)\s+(\w+(?:\(\d+\))?)", line)
            if m:
                cols[m[1].lower()] = "int" if m[2].upper() == "SERIAL" else m[2].lower()
    for table, col, typ in re.findall(r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+) (\w+(?:\(\d+\))?)", text):
        schema[table.lower()][col.lower()] = typ.lower()
    return schema


def summary(schema: dict, tables: list[str]) -> str:
    """One line per table: name(col type, ...)."""
    return "\n".join(f"{t}({', '.join(f'{c} {ty}' for c, ty in schema[t].items())})" for t in tables)


def _is_number(t: "exp.DataType") -> bool:
    return t.is_type(*exp.DataType.NUMERIC_TYPES)


def check(sql: str, schema: dict) -> str | None:
    """None if every table/column resolves and literal comparisons are type-sane, else an error message."""
    tree = exp.maybe_parse(sql, dialect="postgres")
    unknown = sorted({t.name for t in tree.find_all(exp.Table) if t.db == "omop" and t.name.lower() not in schema})
    if unknown:
        return f"Unknown OMOP table(s): {', '.join(unknown)}."
    try:
        tree = qualify(
            tree, schema={"omop": schema}, dialect="postgres", validate_qualify_columns=True, quote_identifiers=False
        )
    except OptimizeError as e:
        return f"Schema check: {e}. Use only columns that exist in the OMOP CDM 5.4 DDL."
    tree = annotate_types(tree, schema={"omop": schema}, dialect="postgres")
    # A string literal compared with a numeric column (or vice versa) is a Postgres type error at run time.
    for cmp in tree.find_all(exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.In):
        col = cmp.this if isinstance(cmp.this, exp.Column) else None
        lits = cmp.expressions if isinstance(cmp, exp.In) else [cmp.expression]
        if col is None or col.type is None or col.type.is_type(exp.DataType.Type.UNKNOWN):
            continue
        for lit in lits:
            if not isinstance(lit, exp.Literal):
                continue
            if (
                lit.is_string
                and _is_number(col.type)
                or not lit.is_string
                and col.type.is_type(*exp.DataType.TEXT_TYPES)
            ):
                return (
                    f"Type mismatch: {col.sql('postgres')} is {col.type.sql('postgres')} but is compared with "
                    f"{lit.sql('postgres')}."
                )
    return None
