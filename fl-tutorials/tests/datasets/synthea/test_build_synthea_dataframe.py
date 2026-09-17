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

"""The Synthea EHR dataframe build: ``derive_features`` must agree with the tutorial's ``query.sql``.

The tutorial trains on two cohorts that are meant to be the same thing in two languages: on the
platform, ``query.sql`` runs against each trust's OMOP; on the simulator, ``derive_features`` runs
the same rules in pandas over the raw Synthea tables. Nothing structural pins them to each other (a
byte-comparison cannot span SQL and pandas), and the tutorial-app tests start from an already-derived
frame, so a drift here would let ``make run-tutorial`` train on a different cohort than a deployed
run with every test green. This module is that pin: it runs the actual ``query.sql`` on SQLite over
the same tiny tables and diffs the two outputs row for row. The query uses nothing Postgres-only —
schema-qualified names (attached here as ``omop``), ``MAX(CASE …)``, ``COUNT(DISTINCT …)``, ISO text
dates — so SQLite is a faithful stand-in and no service is needed.

The fixture persons are chosen to exercise each rule the docstring of ``derive_features`` states:
pre-diagnosis filtering of both conditions and visits, condition-only cohort inclusion, each
risk-factor flag, the two counts, and the label.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

FL_TUTORIALS_DIR = Path(__file__).resolve().parents[3]
SCRIPT_PATH = FL_TUTORIALS_DIR / "datasets" / "synthea" / "build_synthea_dataframe.py"
# Both backends' copies are byte-identical (scripts/check_tutorial_sync.sh pins the Flower copy to
# the NVFLARE one); test the reference and the copy both, so either drifting from pandas is caught.
QUERY_FILES = {
    "nvflare": FL_TUTORIALS_DIR / "nvflare" / "tabular_classification" / "ehr_risk_prediction" / "query.sql",
    "flower": FL_TUTORIALS_DIR / "flower" / "ehr_risk_prediction" / "query.sql",
}


@pytest.fixture(scope="module")
def build() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_synthea_dataframe", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------------------------
# Fixture persons. Codes are the SNOMED strings the script and the query both match on.
#   1  diagnosed T2DM in 2015; history before AND after the diagnosis, only the "before" counts
#   2  never diagnosed; whole history counts (hypertension, hyperlipidemia, 3 visits)
#   3  history-free: a visit but no condition row -> excluded by the condition-only inclusion
#   4  diagnosed T2DM with nothing before it: label 1, every flag 0, both counts 0
#   5  never diagnosed, one condition that is no risk factor: flags 0, n_prior_conditions 1
# ---------------------------------------------------------------------------------------------
T2DM = "44054006"
PREDIABETES = "15777000"
OBESITY = "162864005"
SEVERE_OBESITY = "408512008"
HYPERTENSION = "38341003"
HYPERLIPIDEMIA = "55822004"
UNRELATED = "195967001"  # asthma: recorded, but not one of the flagged risk factors


@pytest.fixture
def tables() -> dict[str, pd.DataFrame]:
    person = pd.DataFrame(
        {
            "person_id": [1, 2, 3, 4, 5],
            "gender_concept_id": [8532, 8507, 8532, 8507, 8532],
            "year_of_birth": [1960, 1985, 1990, 1970, 2000],
        }
    )
    condition = pd.DataFrame(
        [
            # person 1: pre-dx prediabetes + obesity (twice: distinct-count must not double count),
            # the diagnosis itself, then post-dx hypertension that must NOT count.
            (1, PREDIABETES, "2010-05-01"),
            (1, OBESITY, "2012-01-01"),
            (1, OBESITY, "2013-01-01"),
            (1, T2DM, "2015-06-30"),
            (1, HYPERTENSION, "2016-01-01"),
            (1, SEVERE_OBESITY, "2015-06-30"),  # same day as the diagnosis: strictly-before excludes it
            # person 2: never diagnosed, everything counts.
            (2, HYPERTENSION, "2018-03-03"),
            (2, HYPERLIPIDEMIA, "2019-04-04"),
            # person 4: the diagnosis is the first and only condition.
            (4, T2DM, "2020-01-01"),
            # person 5: one unflagged condition.
            (5, UNRELATED, "2021-02-02"),
        ],
        columns=["person_id", "condition_source_value", "condition_start_date"],
    )
    visit = pd.DataFrame(
        [
            (1, "2011-01-01"),
            (1, "2014-12-31"),
            (1, "2015-06-30"),  # diagnosis day: excluded
            (1, "2017-01-01"),  # post-dx: excluded
            (2, "2018-01-01"),
            (2, "2019-01-01"),
            (2, "2020-01-01"),
            (3, "2019-05-05"),  # person 3 has visits but no conditions
            (4, "2021-01-01"),  # post-dx only
        ],
        columns=["person_id", "visit_start_date"],
    )
    return {"person": person, "condition_occurrence": condition, "visit_occurrence": visit}


EXPECTED = pd.DataFrame(
    [
        # person_id, accession_id, age, is_female, prediab, obesity, severe, htn, lipid, n_cond, n_visits, label
        (1, "1", 63, 1, 1, 1, 0, 0, 0, 2, 2, 1),
        (2, "2", 38, 0, 0, 0, 0, 1, 1, 2, 3, 0),
        (4, "4", 53, 0, 0, 0, 0, 0, 0, 0, 0, 1),
        (5, "5", 23, 1, 0, 0, 0, 0, 0, 1, 0, 0),
    ],
    columns=[
        "person_id",
        "accession_id",
        "age",
        "is_female",
        "has_prediabetes",
        "has_obesity",
        "has_severe_obesity",
        "has_hypertension",
        "has_hyperlipidemia",
        "n_prior_conditions",
        "n_prior_visits",
        "label_t2dm",
    ],
)


def _normalised(frame: pd.DataFrame) -> pd.DataFrame:
    """Same rows in a comparable shape: ordered by person_id, integer columns as int64, str ids."""
    out = frame.sort_values("person_id").reset_index(drop=True)
    for column in out.columns:
        if column == "accession_id":
            out[column] = out[column].astype(str)
        else:
            out[column] = out[column].astype("int64")
    return out


def _run_query_on_sqlite(query_file: Path, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Run the tutorial's query.sql, unmodified, over the fixture tables in an in-memory SQLite."""
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS omop")
    for name, frame in tables.items():
        # pandas' plain-sqlite3 writer ignores schema=, so stage in main and move under omop.
        frame.to_sql(name, conn, index=False)
        conn.execute(f"CREATE TABLE omop.{name} AS SELECT * FROM main.{name}")
        conn.execute(f"DROP TABLE main.{name}")
    try:
        return pd.read_sql_query(query_file.read_text(encoding="utf-8"), conn)
    finally:
        conn.close()


def test_derive_features_matches_the_stated_rules(build: ModuleType, tables: dict[str, pd.DataFrame]):
    """Each rule, asserted directly so a failure names the rule rather than just 'differs from SQL'."""
    frame = build.derive_features(tables)

    assert list(frame.columns) == list(EXPECTED.columns)
    assert 3 not in set(frame["person_id"]), "a person with visits but no condition row must be excluded"
    pd.testing.assert_frame_equal(_normalised(frame), _normalised(EXPECTED))


@pytest.mark.parametrize("backend", sorted(QUERY_FILES))
def test_derive_features_matches_query_sql_row_for_row(
    build: ModuleType, tables: dict[str, pd.DataFrame], backend: str
):
    """The pandas cohort and the SQL cohort are the same cohort — the guarantee the tutorial rests on."""
    from_sql = _run_query_on_sqlite(QUERY_FILES[backend], tables)
    from_pandas = build.derive_features(tables)

    assert list(from_sql.columns) == list(from_pandas.columns), "column order/names drifted between the two"
    pd.testing.assert_frame_equal(_normalised(from_sql), _normalised(from_pandas))


def test_the_sql_side_is_the_expected_cohort_too(tables: dict[str, pd.DataFrame]):
    """Guards the guard: if both sides drifted the same way the parity test would still pass."""
    from_sql = _run_query_on_sqlite(QUERY_FILES["nvflare"], tables)
    pd.testing.assert_frame_equal(_normalised(from_sql), _normalised(EXPECTED))


def test_write_outputs_splits_sites_by_person_id_modulo(
    build: ModuleType, tables: dict[str, pd.DataFrame], tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """site<k> holds person_id % num_sites == k-1 — the convention the trust loader also uses."""
    frame = build.derive_features(tables)
    build.write_outputs(frame, tmp_path, num_sites=2)

    everyone = pd.read_csv(tmp_path / "dataframe.csv")
    site1 = pd.read_csv(tmp_path / "site1" / "dataframe.csv")
    site2 = pd.read_csv(tmp_path / "site2" / "dataframe.csv")
    assert sorted(everyone["person_id"]) == [1, 2, 4, 5]
    assert sorted(site1["person_id"]) == [2, 4]
    assert sorted(site2["person_id"]) == [1, 5]
    assert list(site1.columns) == list(everyone.columns) == list(EXPECTED.columns)
    # Too few positives per site to train on: the build says so rather than leaving it to the AUROC.
    assert "positives" in capsys.readouterr().err
