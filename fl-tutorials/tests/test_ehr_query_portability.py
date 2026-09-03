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

"""Static guards on the EHR cohort query's portability across OMOP datasets (#1148).

Where a SNOMED code lives, and which code a Synthea version emits, are decided by whoever built
the OMOP dataset. Get either wrong and the feature is silently constant: the cohort still returns
the right number of rows and the column is simply 0 for everyone.

**Held-out AUROC does not catch this.** Measured on a regenerated Synthea/OHDSI dataset, the
pre-#1148 query left ``has_obesity``, ``has_severe_obesity`` and ``has_hypertension`` dead for
every person and still scored 0.941 — Synthea's scripted prediabetes->T2DM progression carries
the signal. Hence these are structural assertions, not a metric threshold.

Static by design: they parse ``query.sql`` and the local-sim builder, so they need no database
and no dataset download.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from types import ModuleType

import pytest
from tutorial_apps import TUTORIALS_ROOT

NVFLARE_QUERY = TUTORIALS_ROOT / "nvflare" / "tabular_classification" / "ehr_risk_prediction" / "query.sql"
FLOWER_QUERY = TUTORIALS_ROOT / "flower" / "ehr_risk_prediction" / "query.sql"
BUILDER = TUTORIALS_ROOT / "datasets" / "synthea" / "build_synthea_dataframe.py"


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_synthea_dataframe", BUILDER)
    assert spec is not None, f"cannot load {BUILDER}"
    assert spec.loader is not None, f"cannot load {BUILDER}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_synthea_dataframe"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sql() -> str:
    return NVFLARE_QUERY.read_text()


def _codes_for(sql_text: str, feature: str) -> set[str]:
    """The SNOMED codes query.sql accepts for one feature flag."""
    match = re.search(rf"THEN 1 ELSE 0 END\) AS {feature}\b", sql_text)
    assert match, f"{feature} is not pivoted in query.sql"
    clause = sql_text[: match.start()].rsplit("CASE WHEN", 1)[-1]
    return set(re.findall(r"'(\d+)'", clause))


def test_both_backend_copies_are_identical():
    """check_tutorial_sync.sh pins these; assert it here too so a drift fails the unit suite."""
    assert NVFLARE_QUERY.read_text() == FLOWER_QUERY.read_text()


def test_risk_factors_are_read_from_both_domain_tables(sql: str):
    """Where a SNOMED code lands is an ETL decision, so the flags must look in both tables.

    The BMI codes are SNOMED *findings*: a domain-aware ETL (OHDSI ETL-Synthea) routes them to
    OBSERVATION, while the published AWS Synthea-in-OMOP export put every code in
    CONDITION_OCCURRENCE with concept_id 0. Reading only one table drops the obesity features
    on one of the two datasets, with no error.
    """
    events_cte = sql.split("risk_factor_events AS (", 1)[1].split("),", 1)[0]
    assert "omop.condition_occurrence" in events_cte
    assert "omop.observation" in events_cte
    assert "UNION ALL" in events_cte


def test_comorbidity_count_stays_conditions_only(sql: str):
    """n_prior_conditions must NOT union observation, or it changes meaning between datasets.

    Synthea emits socioeconomic findings (employment, housing, social isolation) as observations.
    Counting those as comorbidities would make the feature mean one thing on a domain-aware
    dataset and something else on a flat one.
    """
    counts_cte = sql.split("prior_condition_counts AS (", 1)[1].split("),", 1)[0]
    assert "omop.condition_occurrence" in counts_cte
    assert "omop.observation" not in counts_cte


def test_hypertension_accepts_both_synthea_spellings(sql: str):
    """38341003 in the published 1k export, 59621000 from Synthea v3.3.0 — accept both."""
    assert _codes_for(sql, "has_hypertension") == {"38341003", "59621000"}


def test_query_and_local_sim_builder_agree_on_every_code(sql: str):
    """The deployed query and the local-sim CSV must select the same people.

    query.sql runs against a trust's OMOP; build_synthea_dataframe.py mirrors it for the
    no-platform path. If they drift, the tutorial trains on different features depending on how
    it was launched — which is exactly the failure this module exists to prevent.
    """
    builder = _load_builder()
    for feature, codes in builder.CONDITION_FLAGS.items():
        assert _codes_for(sql, feature) == set(codes), (
            f"{feature}: query.sql accepts {_codes_for(sql, feature)}, "
            f"build_synthea_dataframe.py accepts {set(codes)}"
        )
    assert f"'{builder.T2DM_CODE}'" in sql, "the label code differs between query.sql and the builder"


def test_every_feature_in_config_is_produced_by_the_query(sql: str):
    """A feature the query never projects arrives as a missing column, not a silent zero —
    but a feature listed in config.json and absent from the query is still a wiring bug."""
    config = json.loads((FLOWER_QUERY.parent / "app" / "config.json").read_text())
    for feature in config["FEATURES"]:
        assert feature in sql, f"config.json lists {feature!r} but query.sql never projects it"
