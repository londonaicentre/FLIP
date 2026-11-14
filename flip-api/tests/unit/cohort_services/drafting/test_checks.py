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

from unittest.mock import MagicMock

import pytest

from flip_api.cohort_services.drafting import checks
from flip_api.cohort_services.drafting.checks import check_concepts, check_draft, safe_concept_ids
from flip_api.cohort_services.drafting.model import fake_sql

IMAGING = "SELECT io.person_id, io.accession_id FROM omop.image_occurrence io WHERE io.modality_concept_id = 4300757"


def test_imaging_draft_passes():
    assert check_draft(IMAGING, has_imaging=True) is None


def test_imaging_draft_needs_accession_id():
    sql = "SELECT io.person_id FROM omop.image_occurrence io"
    assert "missing accession_id" in check_draft(sql, has_imaging=True)


def test_tabular_draft_needs_person_id_only():
    assert check_draft("SELECT p.person_id AS person_id FROM omop.person p", has_imaging=False) is None
    assert "missing person_id" in check_draft("SELECT p.year_of_birth FROM omop.person p", has_imaging=False)


def test_star_is_rejected():
    assert "do not use *" in check_draft("SELECT * FROM omop.image_occurrence", has_imaging=True)


def test_quoted_name_must_match_exactly():
    sql = 'SELECT io.person_id, io.accession_id AS "Accession_ID" FROM omop.image_occurrence io'
    assert "missing accession_id" in check_draft(sql, has_imaging=True)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("", "no SQL"),
        ("DELETE FROM omop.person", "Only SELECT"),
        ("SELECT 1; SELECT 2", "Exactly one"),
        ("SELECT p.person_id, p.nhs_number FROM omop.person p", "Unknown column"),
    ],
)
def test_hub_precheck_and_schema_failures_are_reported(sql, expected):
    assert expected in check_draft(sql, has_imaging=False)


@pytest.mark.parametrize("imaging", [True, False])
def test_fake_drafts_pass(imaging):
    assert check_draft(fake_sql(imaging), has_imaging=imaging) is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT person_id FROM omop.person",
        "SELECT DISTINCT co.person_id FROM omop.condition_occurrence co",
        "WITH dx AS (SELECT co.person_id FROM omop.condition_occurrence co) SELECT dx.person_id FROM dx",
        "SELECT p.person_id FROM omop.person p UNION SELECT co.person_id FROM omop.condition_occurrence co",
    ],
)
def test_identifier_read_from_omop_passes(sql):
    assert check_draft(sql, has_imaging=False) is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CAST(p.person_id AS INT) AS person_id FROM omop.person p",
        "SELECT co.visit_occurrence_id AS person_id FROM omop.condition_occurrence co",
        "SELECT MIN(co.person_id) AS person_id FROM omop.condition_occurrence co GROUP BY co.condition_source_value",
        "WITH c AS (SELECT 1 AS person_id) SELECT c.person_id FROM c",
        "SELECT x.person_id FROM (SELECT p.person_id + 0 AS person_id FROM omop.person p) x",
        "SELECT p.person_id FROM omop.person p UNION SELECT 1",
    ],
)
def test_computed_or_renamed_identifier_is_rejected(sql):
    assert "must be the person_id column of an OMOP table" in check_draft(sql, has_imaging=False)


def test_accession_id_is_checked_too():
    sql = "SELECT io.person_id, CAST(io.image_occurrence_id AS VARCHAR) AS accession_id FROM omop.image_occurrence io"
    assert "must be the accession_id column" in check_draft(sql, has_imaging=True)


def test_identifier_check_steps_aside_when_lineage_fails(monkeypatch):
    monkeypatch.setattr(checks, "lineage", MagicMock(side_effect=RuntimeError("gap")))
    assert check_draft(IMAGING, has_imaging=True) is None


def test_safe_list_is_read_from_the_prompt():
    assert {"4300757", "8532", "1147304"} <= safe_concept_ids()


@pytest.mark.parametrize(
    ("concepts_used", "assumptions", "expected"),
    [
        (["4300757 = CT", "8532 = female"], [], None),
        (["201826 = type 2 diabetes"], ["201826 is unverified"], None),
        (["201826 = type 2 diabetes"], [], "201826 is not on the safe list"),
        (["LOINC 4548-4 = HbA1c", "3038553 = BMI"], ["3038553 is a guess"], "4548-4 is not on the safe list"),
        (["44054006 = T2DM", "38341003 = hypertension"], [], "44054006, 38341003 are not on the safe list"),
        (["'Chest X-ray' = procedure_source_value"], [], None),
    ],
)
def test_codes_off_the_safe_list_must_be_flagged(concepts_used, assumptions, expected):
    problem = check_concepts(concepts_used, assumptions)
    assert problem is None if expected is None else expected in problem
