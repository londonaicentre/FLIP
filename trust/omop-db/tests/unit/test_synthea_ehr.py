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
"""Unit tests for the Synthea-in-OMOP EHR loader's transforms (no database required)."""

import pandas as pd
import pytest

from omop_db_tools.synthea_ehr import (
    PERSON_ID_OFFSET,
    REQUIRED_SOURCE_COLUMNS,
    _trust_person_ids,
    build_condition_rows,
    build_person_rows,
    build_visit_rows,
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


def test_offset_is_large_and_even():
    # Even → the person_id-modulo per-trust split is unchanged by the shift; large → no collision
    # with the small imaging-cohort ids already in the mock OMOP.
    assert PERSON_ID_OFFSET % 2 == 0
    assert PERSON_ID_OFFSET > 1_000_000


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
