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
"""The Snowflake DDL is derived from the Postgres DDL, and covers all of it (FLIP#1580)."""

from pathlib import Path

import pytest

from omop_db_tools.snowflake_ddl import (
    DEFAULT_POSTGRES_DDL,
    DdlTranslationError,
    parse_postgres_ddl,
    postgres_table_names,
    read_ddl,
    snowflake_ddl_statements,
)

REPO_DDL = Path(__file__).resolve().parents[2] / DEFAULT_POSTGRES_DDL


@pytest.fixture(scope="module")
def shipped_ddl() -> str:
    return REPO_DDL.read_text()


def test_every_postgres_table_is_created_in_snowflake(shipped_ddl: str) -> None:
    """The coverage gate: a table in the Postgres DDL that the translation misses would be a
    Snowflake trust silently short of a table, surfacing much later as an empty cohort."""
    statements = snowflake_ddl_statements(shipped_ddl, "FLIP_TRUST_GSTT.OMOP")
    created = {
        statement.split("FLIP_TRUST_GSTT.OMOP.")[1].split()[0].split("(")[0].lower()
        for statement in statements
        if statement.startswith("CREATE TABLE")
    }
    assert created == postgres_table_names(shipped_ddl)
    assert len(created) >= 41


def test_imaging_extension_columns_survive(shipped_ddl: str) -> None:
    """accession_id arrives as an ALTER in the Postgres DDL; it must still reach Snowflake."""
    statements = snowflake_ddl_statements(shipped_ddl, "FLIP_TRUST_GSTT.OMOP")
    alters = [s for s in statements if s.startswith("ALTER TABLE")]
    assert any("IMAGE_OCCURRENCE" in s and "accession_id VARCHAR(255)" in s for s in alters)
    assert all("IF NOT EXISTS" in s for s in alters)


def test_types_are_mapped_not_copied(shipped_ddl: str) -> None:
    tables, _ = parse_postgres_ddl(shipped_ddl)
    person = next(table for table in tables if table.name == "person")
    columns = dict(column.split(" ", 1) for column in person.columns)
    assert columns["person_id"].startswith("NUMBER(38,0)")
    assert "PRIMARY KEY" in columns["person_id"]
    assert columns["birth_datetime"] == "TIMESTAMP_NTZ"
    assert columns["person_source_value"] == "VARCHAR(50)"
    # No Postgres-only spelling leaks through anywhere.
    rendered = "\n".join(snowflake_ddl_statements(shipped_ddl, "X.Y"))
    for forbidden in ("SERIAL", "TIMESTAMP ", "integer", "NUMERIC"):
        assert forbidden not in rendered


def test_bare_numeric_keeps_a_scale(shipped_ddl: str) -> None:
    """Snowflake's bare NUMBER is scale 0: value_as_number would silently round."""
    tables, _ = parse_postgres_ddl(shipped_ddl)
    measurement = next(table for table in tables if table.name == "measurement")
    assert "value_as_number NUMBER(38,9)" in measurement.columns


def test_quoted_reserved_column_keeps_its_quotes(shipped_ddl: str) -> None:
    tables, _ = parse_postgres_ddl(shipped_ddl)
    cost = next(table for table in tables if table.name == "note_nlp")
    assert any(column.startswith('"offset" ') for column in cost.columns)


def test_unmapped_type_raises_rather_than_guessing() -> None:
    with pytest.raises(DdlTranslationError, match="No Snowflake mapping"):
        parse_postgres_ddl("CREATE TABLE T (c jsonb NULL);")


def test_unsupported_modifier_raises() -> None:
    with pytest.raises(DdlTranslationError, match="Unsupported column modifier"):
        parse_postgres_ddl("CREATE TABLE T (c integer DEFAULT 1);")


def test_unknown_statement_raises() -> None:
    with pytest.raises(DdlTranslationError, match="Unrecognised statement"):
        parse_postgres_ddl("CREATE INDEX idx ON person (person_id);")


def test_read_ddl_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="OMOP DDL not found"):
        read_ddl(tmp_path / "nope.sql")
