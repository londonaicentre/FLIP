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
# The x-ray tutorials' vocabulary-free cohort query must hand the app the same cohort as the real one.
#
# A trust deployed without the OMOP vocabulary (the Helm chart's NO_VOCAB_LOAD=1, FLIP#1390) holds the
# x-ray data but only a sliver of omop.concept, so query.sql — which names the Yes/No answers, the
# modality and the anatomy through concept joins — returns zero rows there and staging refuses the
# project. query_no_vocab.sql names the two answer concepts inline instead. It is a test-trust query,
# never the tutorial's canonical one, and it is only honest while it selects the same studies with the
# same findings: so both run here against one synthetic OMOP database (SQLite, which speaks every
# construct the two use) and must agree on everything the app reads.

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest

TUTORIALS = Path(__file__).resolve().parents[1]
MAPPINGS_PATH = TUTORIALS / "datasets" / "utils" / "omop_mappings.py"
QUERY_DIRS = {
    "nvflare": TUTORIALS / "nvflare" / "image_classification" / "xray_classification",
    "flower": TUTORIALS / "flower" / "xray_classification",
}
# What the app consumes: accession_id to fetch the study, the three findings as labels, and person_id
# for the trust's per-subject age/sex distributions.
APP_COLUMNS = ("person_id", "accession_id", "Effusion", "Edema", "Lungs in normal arrangement")

X_RAY = 4056681  # the modality the cxr converter writes; any concept works, the query only names it
LIMIT = 300


def _load_mappings() -> ModuleType:
    module_name = "fl_tutorials_under_test.omop_mappings_xray_query"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, MAPPINGS_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {MAPPINGS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


MAPPINGS = _load_mappings()
YES, NO = MAPPINGS.MAPPING_YES_NO["yes"], MAPPINGS.MAPPING_YES_NO["no"]
EFFUSION = MAPPINGS.MAPPING_FINDING["pleural effusion"]
EDEMA = MAPPINGS.MAPPING_FINDING["pulmonary edema"]
NORMAL = MAPPINGS.MAPPING_FINDING["normal lungs"]
OBSERVATION_FIELD = MAPPINGS.MAPPING_CXR["observation"]
LUNG = MAPPINGS.MAPPING_CXR["lung_structure"]


def _omop(*, with_vocabulary: bool) -> sqlite3.Connection:
    """A synthetic omop schema: studies with every mix of answered, unanswered and absent findings."""
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH DATABASE ':memory:' AS omop")
    db.executescript(
        """
        CREATE TABLE omop.concept (concept_id INTEGER PRIMARY KEY, concept_name TEXT);
        CREATE TABLE omop.image_occurrence (
            image_occurrence_id INTEGER PRIMARY KEY, person_id INTEGER, accession_id TEXT,
            image_occurrence_date TEXT, modality_concept_id INTEGER, anatomic_site_concept_id INTEGER);
        CREATE TABLE omop.image_feature (
            image_feature_id INTEGER PRIMARY KEY, image_occurrence_id INTEGER, image_feature_concept_id INTEGER,
            anatomic_site_concept_id INTEGER, image_feature_event_id INTEGER,
            image_feature_event_field_concept_id INTEGER);
        CREATE TABLE omop.observation (observation_id INTEGER PRIMARY KEY, value_as_concept_id INTEGER);
        """
    )
    if with_vocabulary:
        db.executemany(
            "INSERT INTO omop.concept VALUES (?, ?)",
            [(YES, "Yes"), (NO, "No"), (X_RAY, "X-ray"), (LUNG, "Lung structure")],
        )
    answers = [YES, NO, None]  # None: the finding was not recorded for that study
    feature_id = observation_id = 0
    # More studies than LIMIT, so the two queries must also agree on which studies the cut keeps.
    for study in range(1, LIMIT + 40):
        db.execute(
            "INSERT INTO omop.image_occurrence VALUES (?, ?, ?, ?, ?, ?)",
            (study, 1000 + study // 2, f"ACC{study:05d}", "2026-01-01", X_RAY, LUNG),
        )
        for offset, finding in enumerate((EFFUSION, EDEMA, NORMAL)):
            answer = answers[(study + offset) % 3]
            if answer is None:
                continue
            feature_id += 1
            observation_id += 1
            db.execute("INSERT INTO omop.observation VALUES (?, ?)", (observation_id, answer))
            db.execute(
                "INSERT INTO omop.image_feature VALUES (?, ?, ?, ?, ?, ?)",
                (feature_id, study, finding, LUNG, observation_id, OBSERVATION_FIELD),
            )
        # A feature row that points at another table must not count as an answer in either query.
        feature_id += 1
        db.execute(
            "INSERT INTO omop.image_feature VALUES (?, ?, ?, ?, ?, ?)",
            (feature_id, study, EFFUSION, LUNG, 999_999, OBSERVATION_FIELD + 1),
        )
    return db


def _run(db: sqlite3.Connection, query: Path) -> list[dict]:
    cursor = db.execute(query.read_text())
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _app_view(rows: list[dict]) -> list[tuple]:
    return [tuple(row[column] for column in APP_COLUMNS) for row in rows]


@pytest.mark.parametrize("backend", sorted(QUERY_DIRS))
def test_the_no_vocab_query_sits_beside_the_canonical_one(backend):
    assert (QUERY_DIRS[backend] / "query.sql").is_file()
    assert (QUERY_DIRS[backend] / "query_no_vocab.sql").is_file()


def test_both_backends_ship_the_same_no_vocab_query():
    nvflare, flower = (QUERY_DIRS[b] / "query_no_vocab.sql" for b in ("nvflare", "flower"))
    assert nvflare.read_text() == flower.read_text()


@pytest.mark.parametrize("backend", sorted(QUERY_DIRS))
def test_without_a_vocabulary_only_the_no_vocab_query_returns_the_cohort(backend):
    """The failure it exists for: on a NO_VOCAB_LOAD trust the canonical query is empty."""
    db = _omop(with_vocabulary=False)

    assert _run(db, QUERY_DIRS[backend] / "query.sql") == []
    assert len(_run(db, QUERY_DIRS[backend] / "query_no_vocab.sql")) == LIMIT


@pytest.mark.parametrize("backend", sorted(QUERY_DIRS))
def test_the_no_vocab_query_hands_the_app_the_same_cohort(backend):
    canonical = _run(_omop(with_vocabulary=True), QUERY_DIRS[backend] / "query.sql")
    no_vocab = _run(_omop(with_vocabulary=False), QUERY_DIRS[backend] / "query_no_vocab.sql")

    assert len(canonical) == LIMIT
    assert _app_view(no_vocab) == _app_view(canonical)


def test_the_synthetic_cohort_exercises_every_answer():
    """Guards the fixture: agreement over a cohort of one answer would prove nothing."""
    rows = _run(_omop(with_vocabulary=True), QUERY_DIRS["nvflare"] / "query.sql")

    for finding in ("Effusion", "Edema", "Lungs in normal arrangement"):
        assert {row[finding] for row in rows} == {"Yes", "No", None}
