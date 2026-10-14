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
"""Unit tests for the ADNI biomarker-cohort loader's transforms and provenance-scoped reload.

No ADNI value appears here: the cohort fixture is invented in the converter's column shape. The
reload test runs the loader's actual SQL against an in-memory SQLite database with an attached
"omop" schema standing in for Postgres — no external service, so it stays a unit test.
"""

import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text

from omop_db_tools import adni_cohort
from omop_db_tools.adni_cohort import (
    ADNI_SOURCE_VALUE_PREFIX,
    BASELINE_DATE,
    COHORT_COLUMNS,
    MEASUREMENT_CODES,
    PERSON_ID_OFFSET,
    _trust_slice,
    build_measurement_rows,
    build_observation_rows,
    build_person_rows,
    load_adni_cohort,
    main,
    read_cohort,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
QUERY_SQL = REPO_ROOT / "fl-tutorials/nvflare/tabular_causal_discovery/biomarker_causal_discovery/query.sql"


@pytest.fixture
def cohort() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "person_id": [1, 2, 3, 4],
            "age": [71, 68, 80, 75],
            "is_female": [1, 0, 1, 0],
            "education_years": [16, 12, 20, 13],
            "apoe4_copies": [1, 0, 0, 2],
            "plasma_ab42_ab40_ratio": [0.08, 0.1, 0.09, 0.07],
            "plasma_ptau217": [0.2, 0.1, 0.5, 0.4],
            "plasma_nfl": [20.0, 15.0, 30.0, 25.0],
            "plasma_gfap": [150.0, 100.0, 200.0, 180.0],
            "icv_mm3": [1_500_000.0, 1_400_000.0, 1_450_500.0, 1_600_000.0],
            "hippocampus_mm3": [7300.0, 6100.0, 7650.0, 6700.0],
            "adas_cog13": [12.0, 8.0, 20.5, 30.0],
        }
    )


def test_offset_clears_the_imaging_ceiling_and_the_synthea_band():
    assert PERSON_ID_OFFSET > 999_999_999
    assert PERSON_ID_OFFSET > 1_000_000_000 + 100_000  # Synthea's 1k persons sit just above 1e9
    assert PERSON_ID_OFFSET + 10_000_000 < 2_147_483_647


def test_measurement_codes_are_the_ones_query_sql_matches():
    """The loader's codes must be the strings the tutorial's cohort query filters on."""
    query = QUERY_SQL.read_text()
    for code in [*MEASUREMENT_CODES.values(), adni_cohort.EDUCATION_CODE]:
        assert f"'{code}'" in query, code


def test_trust_slice_matches_the_converters_site_split(cohort: pd.DataFrame):
    site1 = _trust_slice(cohort, 2, 1)
    site2 = _trust_slice(cohort, 2, 2)
    assert site1["person_id"].tolist() == [2, 4]  # person_id % 2 == 0 is site 1 / trust 1
    assert site2["person_id"].tolist() == [1, 3]


def test_build_person_rows_preserves_age_and_marks_provenance(cohort: pd.DataFrame):
    rows = build_person_rows(cohort)
    assert rows["person_id"].tolist() == [PERSON_ID_OFFSET + p for p in (1, 2, 3, 4)]
    assert rows["gender_concept_id"].tolist() == [8532, 8507, 8532, 8507]
    assert (BASELINE_DATE.year - rows["year_of_birth"]).tolist() == cohort["age"].tolist()
    assert rows["birth_datetime"].notna().all()  # data-access-api's age statistic reads it
    assert rows["person_source_value"].tolist() == [f"{ADNI_SOURCE_VALUE_PREFIX}{p}" for p in (1, 2, 3, 4)]
    assert (rows["race_concept_id"] == 0).all()


def test_build_measurement_rows_one_per_person_and_code(cohort: pd.DataFrame):
    rows = build_measurement_rows(cohort)
    assert len(rows) == len(cohort) * len(MEASUREMENT_CODES)
    assert rows["measurement_id"].is_unique
    assert (rows["measurement_id"] > PERSON_ID_OFFSET).all()
    assert (rows["measurement_concept_id"] == 0).all()
    assert (rows["measurement_type_concept_id"] == 0).all()
    ptau = rows[rows["measurement_source_value"] == "PLASMA_PTAU217"].sort_values("person_id")
    assert ptau["value_as_number"].tolist() == cohort["plasma_ptau217"].tolist()
    assert ptau["person_id"].tolist() == [PERSON_ID_OFFSET + p for p in (1, 2, 3, 4)]


def test_build_observation_rows_carry_education(cohort: pd.DataFrame):
    rows = build_observation_rows(cohort)
    assert rows["observation_source_value"].eq("EDUCATION_YEARS").all()
    assert rows["value_as_number"].tolist() == [16.0, 12.0, 20.0, 13.0]
    assert rows["observation_id"].is_unique


def test_read_cohort_names_missing_columns(tmp_path: Path, cohort: pd.DataFrame):
    bad = tmp_path / "dataframe.csv"
    cohort.drop(columns=["plasma_gfap"]).to_csv(bad, index=False)
    with pytest.raises(SystemExit, match="plasma_gfap"):
        read_cohort(bad)
    good = tmp_path / "ok.csv"
    cohort.to_csv(good, index=False)
    assert list(read_cohort(good).columns) == COHORT_COLUMNS


class TestReloadByProvenance:
    """A reload replaces the previous load and leaves everyone else's rows alone."""

    IMAGING_PERSON_ID = 923_226_025
    SYNTHEA_PERSON_ID = 1_000_000_007

    @pytest.fixture
    def engine(self):
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
                    "CREATE TABLE omop.measurement (measurement_id INTEGER PRIMARY KEY, person_id INTEGER, "
                    "measurement_concept_id INTEGER, measurement_date TEXT, measurement_type_concept_id INTEGER, "
                    "value_as_number REAL, measurement_source_value TEXT)"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE omop.observation (observation_id INTEGER PRIMARY KEY, person_id INTEGER, "
                    "observation_concept_id INTEGER, observation_date TEXT, observation_type_concept_id INTEGER, "
                    "value_as_number REAL, observation_source_value TEXT)"
                )
            )
            for pid, source in ((self.IMAGING_PERSON_ID, "NHS123"), (self.SYNTHEA_PERSON_ID, "synthea-7")):
                conn.execute(
                    text(
                        "INSERT INTO omop.person (person_id, gender_concept_id, year_of_birth, race_concept_id, "
                        "ethnicity_concept_id, person_source_value) VALUES (:pid, 8507, 1960, 0, 0, :src)"
                    ),
                    {"pid": pid, "src": source},
                )
                conn.execute(
                    text(
                        "INSERT INTO omop.measurement (measurement_id, person_id, measurement_concept_id, "
                        "measurement_date, measurement_type_concept_id, value_as_number, measurement_source_value) "
                        "VALUES (:mid, :pid, 0, '2020-01-01', 0, 1.0, 'OTHER')"
                    ),
                    {"mid": pid, "pid": pid},
                )
        return engine

    def _counts(self, engine) -> dict[str, int]:
        with engine.connect() as conn:
            return {
                table: conn.execute(text(f"SELECT COUNT(*) FROM omop.{table}")).scalar_one()
                for table in ("person", "measurement", "observation")
            }

    def test_second_load_replaces_the_first_and_spares_other_cohorts(self, engine, cohort: pd.DataFrame):
        assert load_adni_cohort(engine, cohort, num_trusts=2, trust_index=1) == 2
        first = self._counts(engine)
        assert first == {"person": 4, "measurement": 2 + 2 * len(MEASUREMENT_CODES), "observation": 2}

        assert load_adni_cohort(engine, cohort, num_trusts=2, trust_index=1) == 2
        assert self._counts(engine) == first  # idempotent

        with engine.connect() as conn:
            survivors = conn.execute(
                text("SELECT person_id FROM omop.person WHERE person_source_value NOT LIKE 'adni-cohort-%'")
            ).scalars()
            assert sorted(survivors) == sorted([self.IMAGING_PERSON_ID, self.SYNTHEA_PERSON_ID])
            other_measurements = conn.execute(
                text("SELECT COUNT(*) FROM omop.measurement WHERE measurement_source_value = 'OTHER'")
            ).scalar_one()
            assert other_measurements == 2

    def test_trust_two_gets_the_other_slice(self, engine, cohort: pd.DataFrame):
        load_adni_cohort(engine, cohort, num_trusts=2, trust_index=2)
        with engine.connect() as conn:
            loaded = conn.execute(
                text("SELECT person_id FROM omop.person WHERE person_source_value LIKE 'adni-cohort-%'")
            ).scalars()
            assert sorted(loaded) == [PERSON_ID_OFFSET + 1, PERSON_ID_OFFSET + 3]


class TestMain:
    def test_rejects_bad_trust_index(self, tmp_path: Path):
        with pytest.raises(SystemExit, match="trust-index"):
            main(["--cohort", str(tmp_path / "x.csv"), "--trust-index", "3", "--num-trusts", "2"])

    def test_names_a_missing_cohort_file(self, tmp_path: Path):
        with pytest.raises(SystemExit, match="convert-adni-cohort"):
            main(["--cohort", str(tmp_path / "missing.csv"), "--trust-index", "1"])

    def test_wires_settings_and_loader(self, tmp_path: Path, cohort: pd.DataFrame, monkeypatch):
        path = tmp_path / "dataframe.csv"
        cohort.to_csv(path, index=False)
        calls: dict[str, object] = {}

        class FakeSettings:
            OMOP_DATABASE_URL = SecretStr("postgresql://omop-host:5434/omop")

        monkeypatch.setattr(adni_cohort, "get_settings", lambda: FakeSettings())
        monkeypatch.setattr(adni_cohort, "create_engine", lambda url, echo: calls.setdefault("engine", (url, echo)))

        def fake_load(engine, frame, num_trusts, trust_index):
            calls["load"] = (len(frame), num_trusts, trust_index)

        monkeypatch.setattr(adni_cohort, "load_adni_cohort", fake_load)
        main(["--cohort", str(path), "--trust-index", "2", "--num-trusts", "2"])
        assert calls["engine"] == ("postgresql://omop-host:5434/omop", False)
        assert calls["load"] == (4, 2, 2)
