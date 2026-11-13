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

import pytest

from flip_api.cohort_services.drafting.checks import check_draft
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
