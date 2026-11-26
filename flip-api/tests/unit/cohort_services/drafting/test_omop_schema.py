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

from pathlib import Path

import sqlglot

from flip_api.cohort_services.drafting.omop_schema import TABLES, check, load_schema, parse_ddl

DDL = Path(__file__).resolve().parents[5] / "trust/omop-db/files/OMOPCDM_postgresql_5.4_ddl.sql"


def _check(sql: str) -> str | None:
    return check(sqlglot.parse_one(sql, read="postgres"), load_schema())


def test_shipped_schema_matches_the_ddl():
    """The prompt's schema copy must be regenerated when the trust's DDL changes (see the module docstring)."""
    assert load_schema() == parse_ddl(DDL.read_text())
    assert tuple(load_schema()) == TABLES


def test_imaging_extension_columns_are_present():
    assert {"accession_id", "modality_concept_id"} <= set(load_schema()["image_occurrence"])


def test_valid_query_passes():
    assert _check("SELECT p.person_id FROM omop.person p WHERE p.gender_concept_id = 8532") is None


def test_unknown_table_is_named():
    assert _check("SELECT n.person_id FROM omop.note n") == "Unknown OMOP table(s): note."


def test_unknown_column_is_rejected():
    assert "Unknown column: nhs_number" in _check("SELECT p.person_id, p.nhs_number FROM omop.person p")


def test_string_literal_against_number_is_rejected():
    assert "Type mismatch" in _check("SELECT p.person_id FROM omop.person p WHERE p.year_of_birth = '1960'")


def test_number_against_varchar_is_rejected():
    sql = "SELECT co.person_id FROM omop.condition_occurrence co WHERE co.condition_source_value = 44054006"
    assert "Type mismatch" in _check(sql)
