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
"""Unit tests for the Synthea-in-OMOP EHR loader's transforms.

Most of these need no database. TestCleanReservedBandProvenance is the exception: it exercises
clean_reserved_band's actual DELETE statements against an in-memory SQLite database standing in for
Postgres (attached under the "omop" schema alias so the loader's schema-qualified SQL is unchanged) —
still no external service, so it stays a unit test.
"""

import sqlite3

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from omop_db_tools.synthea_ehr import (
    PERSON_ID_OFFSET,
    REQUIRED_SOURCE_COLUMNS,
    SYNTHEA_SOURCE_VALUE_PREFIX,
    _trust_person_ids,
    build_condition_rows,
    build_person_rows,
    build_visit_rows,
    load_synthea_ehr,
)


@pytest.fixture
def person() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "person_id": [1, 2, 3, 4],
            "gender_concept_id": [8507, 8532, 9999, 8532],  # 9999 is non-standard → zeroed
            "year_of_birth": [1980, 1975, 2000, 1990],
            "birth_datetime": ["1980-06-15", "1975-01-01", "2000-12-31", "1990-03-03"],
        }
    )


@pytest.fixture
def condition() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "person_id": [1, 1, 2, 4],
            "condition_source_value": ["44054006", "15777000", "38341003", "44054006"],
            "condition_start_date": ["2010-01-01", "2011-02-02", "2012-03-03", "2013-04-04"],
        }
    )


@pytest.fixture
def visit() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "person_id": [1, 2, 4],
            "visit_start_date": ["2010-01-01", "2012-03-03", "2013-04-04"],
            "visit_end_date": ["2010-01-02", None, "2013-04-05"],
        }
    )


def test_offset_is_even_and_clears_the_imaging_ceiling():
    # Even → the person_id-modulo per-trust split (computed on the raw id) is unchanged by the shift.
    # >= 1_000_000_000 → clear of the imaging cohorts' person_id ceiling: nhs_number_to_integer takes
    # only the first 9 digits of a real NHS number, so an imaging person_id can be anywhere up to
    # 999_999_999 (NOT merely "small" — it scatters across the whole 9-digit range, which is exactly
    # what made the old id-band *delete* unsafe; that safety now comes from clean_reserved_band's
    # provenance filter, not from this offset — this assertion only protects the INSERT path).
    assert PERSON_ID_OFFSET % 2 == 0
    assert PERSON_ID_OFFSET >= 1_000_000_000


def test_trust_split_is_disjoint_and_covering(person: pd.DataFrame):
    first = _trust_person_ids(person, num_trusts=2, trust_index=1)
    second = _trust_person_ids(person, num_trusts=2, trust_index=2)
    assert first.isdisjoint(second)
    assert first | second == set(person["person_id"])
    # trust_index 1 gets person_id % 2 == 0, i.e. the even ids.
    assert first == {2, 4}
    assert second == {1, 3}


def test_build_person_rows_offsets_and_sanitises_concepts(person: pd.DataFrame):
    rows = build_person_rows(person, {1, 2, 3})
    assert list(rows["person_id"]) == [1 + PERSON_ID_OFFSET, 2 + PERSON_ID_OFFSET, 3 + PERSON_ID_OFFSET]
    # Standard gender concepts survive; a non-standard one is zeroed to stay FK-safe.
    assert list(rows["gender_concept_id"]) == [8507, 8532, 0]
    assert (rows["race_concept_id"] == 0).all()
    assert (rows["ethnicity_concept_id"] == 0).all()
    # clean_reserved_band's delete keys off this prefix — it must actually be there.
    assert list(rows["person_source_value"]) == [
        f"{SYNTHEA_SOURCE_VALUE_PREFIX}1",
        f"{SYNTHEA_SOURCE_VALUE_PREFIX}2",
        f"{SYNTHEA_SOURCE_VALUE_PREFIX}3",
    ]
    assert set(rows.columns) == {
        "person_id",
        "gender_concept_id",
        "year_of_birth",
        "birth_datetime",
        "race_concept_id",
        "ethnicity_concept_id",
        "person_source_value",
    }


def test_build_person_rows_carries_birth_datetime(person: pd.DataFrame):
    # data-access-api's cohort statistics compute the age distribution from
    # omop.person.birth_datetime — a NULL there 500s every cohort submission for the tutorial,
    # so the loader must populate it and the schema guard must require it upstream.
    rows = build_person_rows(person, {1, 2, 3}).reset_index(drop=True)
    assert rows["birth_datetime"].notna().all()
    assert list(rows["birth_datetime"]) == list(pd.to_datetime(["1980-06-15", "1975-01-01", "2000-12-31"]))
    assert "birth_datetime" in REQUIRED_SOURCE_COLUMNS["person"]


def test_build_condition_rows_keeps_snomed_and_offsets_ids(condition: pd.DataFrame):
    rows = build_condition_rows(condition, {1, 4})
    # Only persons 1 and 4 (3 conditions), source SNOMED string preserved verbatim.
    assert list(rows["condition_source_value"]) == ["44054006", "15777000", "44054006"]
    assert (rows["person_id"] >= PERSON_ID_OFFSET).all()
    assert (rows["condition_occurrence_id"] >= PERSON_ID_OFFSET).all()
    assert rows["condition_occurrence_id"].is_unique
    # concept_id columns zeroed (query.sql matches on condition_source_value, not concept_id).
    assert (rows["condition_concept_id"] == 0).all()
    assert (rows["condition_type_concept_id"] == 0).all()


def test_build_visit_rows_falls_back_end_date_to_start(visit: pd.DataFrame):
    rows = build_visit_rows(visit, {1, 2, 4}).reset_index(drop=True)
    # person 2's visit_end_date is NULL → falls back to its start date, so the NOT NULL column loads.
    assert str(rows.loc[1, "visit_end_date"]) == "2012-03-03"
    assert (rows["person_id"] >= PERSON_ID_OFFSET).all()
    assert rows["visit_occurrence_id"].is_unique


def test_visit_end_date_defaults_to_start_when_column_absent():
    frame = pd.DataFrame({"person_id": [1], "visit_start_date": ["2020-05-05"]})
    rows = build_visit_rows(frame, {1})
    assert str(rows.loc[0, "visit_start_date"]) == "2020-05-05"
    assert str(rows.loc[0, "visit_end_date"]) == "2020-05-05"


class TestCleanReservedBandProvenance:
    """Regression test for the id-band delete bug.

    The imaging cohorts' person_id is nhs_number_to_integer(PatientID) — the first 9 digits of a
    real, effectively random NHS number — so it is not "small"; it scatters across the whole 9-digit
    range. 923226025 is a real person_id from the public spleen export, and it sits in the *old*
    reserved band (>= the retired 900_000_000 threshold): the previous "DELETE WHERE person_id >=
    PERSON_ID_OFFSET" would have destroyed this person, and cascaded away their image_occurrence /
    procedure_occurrence rows with them (fpk_image_occurrence_person_id is ON DELETE CASCADE).
    Measured live: a trust holding 4187 imaging persons lost 422 of them (and their imaging rows) to
    exactly this bug on every `make load-synthea-ehr` reload.

    clean_reserved_band must never delete this person — regardless of where its person_id falls —
    because it deletes by provenance (person_source_value), not by id range.
    """

    IMAGING_PERSON_ID = 923226025

    @pytest.fixture
    def engine(self):
        """An in-memory SQLite database with an attached "omop" schema mimicking the trust OMOP DB."""

        def creator() -> sqlite3.Connection:
            conn = sqlite3.connect(":memory:")
            conn.execute("ATTACH DATABASE ':memory:' AS omop")
            return conn

        engine = create_engine("sqlite://", creator=creator)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE omop.person (person_id INTEGER PRIMARY KEY, gender_concept_id INTEGER, "
                    "year_of_birth INTEGER, birth_datetime TEXT, race_concept_id INTEGER, "
                    "ethnicity_concept_id INTEGER, person_source_value TEXT)"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE omop.condition_occurrence (condition_occurrence_id INTEGER PRIMARY KEY, "
                    "person_id INTEGER, condition_concept_id INTEGER, condition_start_date TEXT, "
                    "condition_type_concept_id INTEGER, condition_source_value TEXT)"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE omop.visit_occurrence (visit_occurrence_id INTEGER PRIMARY KEY, "
                    "person_id INTEGER, visit_concept_id INTEGER, visit_start_date TEXT, "
                    "visit_end_date TEXT, visit_type_concept_id INTEGER)"
                )
            )
            # An imaging-cohort person (real NHS-number-derived id, no "synthea-" provenance marker)
            # with a visit row — exactly what fpk_image_occurrence_person_id's ON DELETE CASCADE
            # stands in for here.
            conn.execute(
                text(
                    "INSERT INTO omop.person "
                    "(person_id, gender_concept_id, year_of_birth, person_source_value) "
                    "VALUES (:pid, 8532, 1990, 'imaging-cohort')"
                ),
                {"pid": self.IMAGING_PERSON_ID},
            )
            conn.execute(
                text(
                    "INSERT INTO omop.visit_occurrence "
                    "(visit_occurrence_id, person_id, visit_concept_id, visit_start_date, "
                    "visit_end_date, visit_type_concept_id) VALUES (1, :pid, 0, '2020-01-01', '2020-01-01', 0)"
                ),
                {"pid": self.IMAGING_PERSON_ID},
            )
        return engine

    def test_imaging_person_in_old_danger_zone_survives_load_and_reload(
        self, engine, person: pd.DataFrame, condition: pd.DataFrame, visit: pd.DataFrame
    ):
        tables = {"person": person, "condition_occurrence": condition, "visit_occurrence": visit}

        load_synthea_ehr(engine, tables, num_trusts=1, trust_index=1)
        load_synthea_ehr(engine, tables, num_trusts=1, trust_index=1)  # reload must stay idempotent

        with engine.connect() as conn:
            surviving_person = conn.execute(
                text("SELECT person_id FROM omop.person WHERE person_id = :pid"),
                {"pid": self.IMAGING_PERSON_ID},
            ).fetchall()
            surviving_visit = conn.execute(
                text("SELECT visit_occurrence_id FROM omop.visit_occurrence WHERE person_id = :pid"),
                {"pid": self.IMAGING_PERSON_ID},
            ).fetchall()
        assert surviving_person, "an imaging person with an id in the old delete band was destroyed by a reload"
        assert surviving_visit, "the imaging person's visit_occurrence row was cascaded away"

    def test_synthea_rows_are_still_replaced_not_duplicated_on_reload(
        self, engine, person: pd.DataFrame, condition: pd.DataFrame, visit: pd.DataFrame
    ):
        tables = {"person": person, "condition_occurrence": condition, "visit_occurrence": visit}

        first_count = load_synthea_ehr(engine, tables, num_trusts=1, trust_index=1)
        second_count = load_synthea_ehr(engine, tables, num_trusts=1, trust_index=1)

        assert first_count == second_count == len(person)
        with engine.connect() as conn:
            (total,) = conn.execute(
                text("SELECT COUNT(*) FROM omop.person WHERE person_source_value LIKE :marker"),
                {"marker": f"{SYNTHEA_SOURCE_VALUE_PREFIX}%"},
            ).one()
        assert total == len(person)  # not doubled by the reload
