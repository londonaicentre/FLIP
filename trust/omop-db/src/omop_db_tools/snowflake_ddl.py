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
"""Derive the Snowflake OMOP CDM DDL from the Postgres DDL this repo already ships (FLIP#1580).

There is ONE schema of record: ``files/OMOPCDM_postgresql_5.4_ddl.sql``, the OHDSI CDM 5.4
Postgres DDL extended with the MI-CDM imaging tables. A Snowflake trust gets the same tables
by translating that file at run time rather than by keeping a second, hand-maintained copy:
a column added to the Postgres DDL (as ``image_occurrence.accession_id`` was) reaches a
Snowflake trust with no second edit, and ``tests/unit/test_snowflake_ddl.py`` asserts the
translation covers every table the Postgres file defines.

The file is machine-written and narrow in form, which is what makes a small explicit
translator preferable to pulling a SQL transpiler into this package: statements are
``CREATE TABLE IF NOT EXISTS <NAME> ( <col> <type> [NULL|NOT NULL], ... );`` plus a handful of
``ALTER TABLE <NAME> ADD COLUMN IF NOT EXISTS <col> <type>;``. Anything else in the file is a
comment, a blank line, or the ``set schema`` Postgres preamble, and anything unrecognised
raises rather than being silently dropped: a table missing from a trust's OMOP would surface
much later as an empty cohort.

Type mapping, and why:

================  ======================  ====================================================
Postgres          Snowflake               Note
================  ======================  ====================================================
SERIAL            NUMBER(38,0)            Snowflake has no serial; the loader supplies ids.
integer / int     NUMBER(38,0)
bigint            NUMBER(38,0)
NUMERIC (bare)    NUMBER(38,9)            Bare NUMBER is scale 0 and would truncate decimals.
NUMERIC(p,s)      NUMBER(p,s)
varchar(n)        VARCHAR(n)
varchar (bare)    VARCHAR
TEXT              VARCHAR                 Snowflake VARCHAR is already variable length.
TIMESTAMP         TIMESTAMP_NTZ           The CSVs carry naive local timestamps.
date              DATE
================  ======================  ====================================================

PRIMARY KEY is carried through. Snowflake does not enforce it, but it keeps the two DDLs
readable side by side and is what ``DESCRIBE TABLE`` then reports. Foreign keys and indices
are deliberately NOT translated: the Postgres constraints/indices files exist because
Postgres needs them for integrity and query plans, while Snowflake enforces neither and plans
with micro-partition pruning instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: The Postgres DDL this package ships, relative to the omop-db directory.
DEFAULT_POSTGRES_DDL = Path("files/OMOPCDM_postgresql_5.4_ddl.sql")

_CREATE_RE = re.compile(
    r"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<body>.*)\)$",
    re.IGNORECASE | re.DOTALL,
)
_ALTER_RE = re.compile(
    r"^ALTER\s+TABLE\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?P<column>[A-Za-z_][A-Za-z0-9_]*)\s+(?P<rest>.+)$",
    re.IGNORECASE | re.DOTALL,
)
#: A column name is a bare identifier or, where it collides with a reserved word (``"offset"``
#: in COST), a double-quoted one. The quotes are carried through: they mean the same thing in
#: Snowflake as in Postgres, and dropping them would make the name unusable.
_COLUMN_RE = re.compile(
    r"^(?P<column>\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)\s+(?P<type>[A-Za-z]+(?:\s*\([^)]*\))?)(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

_SIMPLE_TYPES = {
    "serial": "NUMBER(38,0)",
    "bigserial": "NUMBER(38,0)",
    "integer": "NUMBER(38,0)",
    "int": "NUMBER(38,0)",
    "int4": "NUMBER(38,0)",
    "bigint": "NUMBER(38,0)",
    "int8": "NUMBER(38,0)",
    "smallint": "NUMBER(38,0)",
    "numeric": "NUMBER(38,9)",
    "decimal": "NUMBER(38,9)",
    "float": "FLOAT",
    "double": "FLOAT",
    "real": "FLOAT",
    "text": "VARCHAR",
    "varchar": "VARCHAR",
    "char": "VARCHAR",
    "timestamp": "TIMESTAMP_NTZ",
    "datetime": "TIMESTAMP_NTZ",
    "date": "DATE",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
}

_PARAMETERISED = {
    "numeric": "NUMBER",
    "decimal": "NUMBER",
    "varchar": "VARCHAR",
    "char": "VARCHAR",
}


class DdlTranslationError(ValueError):
    """The Postgres DDL carries a construct this translator will not silently drop."""


@dataclass(frozen=True)
class TableDdl:
    """One translated table.

    Attributes:
        name (str): Table name, lower case (Snowflake upper-cases unquoted names itself).
        columns (tuple[str, ...]): Rendered ``<column> <type> [NOT NULL] [PRIMARY KEY]`` fragments.
    """

    name: str
    columns: tuple[str, ...]

    def create_sql(self, schema: str) -> str:
        """The ``CREATE TABLE IF NOT EXISTS`` statement for this table in ``schema``.

        Args:
            schema (str): Fully qualified schema, e.g. ``FLIP_TRUST_GSTT.OMOP``.

        Returns:
            str: A single SQL statement, terminated with a semicolon.
        """
        body = ",\n    ".join(self.columns)
        return f"CREATE TABLE IF NOT EXISTS {schema}.{self.name.upper()} (\n    {body}\n);"


def _translate_type(raw: str) -> str:
    """Map one Postgres type token (with optional parameters) onto a Snowflake type.

    Args:
        raw (str): Type as written in the Postgres DDL, e.g. ``varchar(50)`` or ``NUMERIC``.

    Returns:
        str: The Snowflake type.

    Raises:
        DdlTranslationError: The type is not in the mapping; a new type in the DDL must be
            mapped deliberately, never guessed.
    """
    compact = re.sub(r"\s+", "", raw)
    match = re.match(r"^([A-Za-z]+)(?:\((.*)\))?$", compact)
    if not match:
        raise DdlTranslationError(f"Unparseable column type: {raw!r}")
    base, params = match.group(1).lower(), match.group(2)
    if base not in _SIMPLE_TYPES:
        raise DdlTranslationError(f"No Snowflake mapping for Postgres type {base!r} (in {raw!r})")
    if params is None:
        return _SIMPLE_TYPES[base]
    if base not in _PARAMETERISED:
        raise DdlTranslationError(f"Postgres type {base!r} does not take parameters (in {raw!r})")
    return f"{_PARAMETERISED[base]}({params})"


def _translate_column(definition: str) -> str:
    """Translate one column definition line.

    Args:
        definition (str): e.g. ``measurement_id SERIAL PRIMARY KEY NOT NULL``.

    Returns:
        str: e.g. ``measurement_id NUMBER(38,0) NOT NULL PRIMARY KEY``.

    Raises:
        DdlTranslationError: The line is not a plain column definition, or carries a modifier
            (DEFAULT, REFERENCES, CHECK, ...) this translator does not handle.
    """
    match = _COLUMN_RE.match(definition.strip())
    if not match:
        raise DdlTranslationError(f"Unparseable column definition: {definition.strip()!r}")
    column = match.group("column")
    snow_type = _translate_type(match.group("type"))
    rest = re.sub(r"\s+", " ", match.group("rest")).strip().upper()

    primary_key = False
    if "PRIMARY KEY" in rest:
        primary_key = True
        rest = rest.replace("PRIMARY KEY", " ").strip()
    not_null = False
    if "NOT NULL" in rest:
        not_null = True
        rest = rest.replace("NOT NULL", " ").strip()
    rest = rest.replace("NULL", " ").strip()
    if rest:
        raise DdlTranslationError(
            f"Unsupported column modifier {rest!r} in {definition.strip()!r}; map it explicitly in snowflake_ddl.py"
        )

    parts = [column, snow_type]
    if not_null:
        parts.append("NOT NULL")
    if primary_key:
        parts.append("PRIMARY KEY")
    return " ".join(parts)


def _split_columns(body: str) -> list[str]:
    """Split a CREATE TABLE body on commas that are not inside type parameters."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part for part in (p.strip() for p in parts) if part]


def _strip_comments(sql: str) -> str:
    """Remove ``--`` line comments; the DDL carries no block comments."""
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def parse_postgres_ddl(sql: str) -> tuple[list[TableDdl], list[str]]:
    """Translate the Postgres OMOP DDL into table definitions plus ALTER fragments.

    Args:
        sql (str): Contents of the Postgres DDL file.

    Returns:
        tuple[list[TableDdl], list[str]]: Tables in file order, and
        ``(table, "<column> <type> [NOT NULL]")`` ALTER fragments rendered as
        ``"<table>|<fragment>"`` strings, also in file order.

    Raises:
        DdlTranslationError: On any statement that is neither a CREATE TABLE, a supported
            ALTER TABLE ADD COLUMN, nor the ``set schema`` preamble.
    """
    tables: list[TableDdl] = []
    alters: list[str] = []
    for raw_statement in _strip_comments(sql).split(";"):
        statement = raw_statement.strip()
        if not statement:
            continue
        if re.match(r"^set\s+schema\b", statement, re.IGNORECASE):
            continue
        create = _CREATE_RE.match(statement)
        if create:
            columns = tuple(_translate_column(part) for part in _split_columns(create.group("body")))
            tables.append(TableDdl(name=create.group("name").lower(), columns=columns))
            continue
        alter = _ALTER_RE.match(statement)
        if alter:
            fragment = _translate_column(f"{alter.group('column')} {alter.group('rest')}")
            alters.append(f"{alter.group('name').lower()}|{fragment}")
            continue
        raise DdlTranslationError(f"Unrecognised statement in the Postgres DDL: {statement[:120]!r}")
    return tables, alters


def snowflake_ddl_statements(sql: str, schema: str) -> list[str]:
    """Every statement needed to create the OMOP CDM tables in a Snowflake schema.

    Args:
        sql (str): Contents of the Postgres DDL file.
        schema (str): Fully qualified target schema, e.g. ``FLIP_TRUST_GSTT.OMOP``.

    Returns:
        list[str]: CREATE TABLE statements in file order, then the ADD COLUMN statements.
    """
    tables, alters = parse_postgres_ddl(sql)
    statements = [table.create_sql(schema) for table in tables]
    for alter in alters:
        table, fragment = alter.split("|", 1)
        statements.append(f"ALTER TABLE IF EXISTS {schema}.{table.upper()} ADD COLUMN IF NOT EXISTS {fragment};")
    return statements


def postgres_table_names(sql: str) -> set[str]:
    """Every table name the Postgres DDL defines, lower case.

    Args:
        sql (str): Contents of the Postgres DDL file.

    Returns:
        set[str]: Table names.
    """
    return {
        match.group(1).lower()
        for match in re.finditer(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)", _strip_comments(sql), re.IGNORECASE
        )
    }


def read_ddl(path: Path | None = None) -> str:
    """Read the Postgres DDL file.

    Args:
        path (Path | None): Override path; defaults to ``DEFAULT_POSTGRES_DDL``.

    Returns:
        str: File contents.

    Raises:
        FileNotFoundError: When the DDL file is not where it is expected (run from trust/omop-db).
    """
    ddl_path = path or DEFAULT_POSTGRES_DDL
    if not ddl_path.is_file():
        raise FileNotFoundError(
            f"OMOP DDL not found at {ddl_path}; run this from trust/omop-db, or pass --ddl-file explicitly"
        )
    return ddl_path.read_text()
