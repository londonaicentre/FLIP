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

"""End-to-end cohort query tests across trust-api → data-access-api → omop-db.

The chain runs without any ``httpx`` / ``make_request`` mocks: data-access-api is a real
process talking to a real Postgres seeded from ``fixtures/omop_seed.sql`` (MI-CDM shape —
``image_occurrence`` joined to ``concept`` for modality lookups). Counts in the seed are
stable, so assertions read like SQL: "12 CT scans in the dataset, expect modality counts
to add up to 12". When a count assertion drifts you should update the seed and the
test together — the comments at the top of the seed call out the expected totals.
"""

import json
from typing import Any

import pytest

from trust_api.services.task_handlers import handle_cohort_query


def _payload(query: str, query_id: str = "qid-1", trust_id: str = "trust_test") -> dict[str, Any]:
    """Compact CohortQueryInput-shaped payload used across every test."""
    return {
        "encrypted_project_id": "enc-project-1",
        "query_id": query_id,
        "query_name": "B3 integration test",
        "query": query,
        "trust_id": trust_id,
    }


def _aggregate(stats: dict[str, Any], name: str) -> dict[str, int]:
    """Pull a single named aggregate (e.g. "Counts") out of the StatisticsResponse data list.

    Returns a ``{value: count}`` mapping. Raises if the aggregate is missing — that's
    the symptom every regression in this code path produces, so a hard failure is right.
    """
    for group in stats["data"]:
        if group["name"] == name:
            return {entry["value"]: int(entry["count"]) for entry in group["results"]}
    raise AssertionError(f"Aggregate {name!r} not found in stats data: {stats['data']}")


# ---------------------------------------------------------------------------
# Happy path — enough rows to clear the threshold; assertions cover the SQL
# template (counts, age + sex distribution) end-to-end.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_image_counts_and_age_sex(stub_hub_received):
    """Full cohort query over the image_occurrence table returns deterministic aggregates."""
    result = await handle_cohort_query(
        _payload("SELECT person_id, modality_concept_id, accession_id FROM omop.image_occurrence")
    )

    assert result == {"success": True}

    # Exactly one POST should have hit the stub hub: the cohort/results callback.
    assert len(stub_hub_received) == 1
    callback = stub_hub_received[0]
    assert callback["path"].rstrip("/").endswith("/cohort/results")

    body = json.loads(callback["body"])
    assert body["query_id"] == "qid-1"
    assert body["trust_id"] == "trust_test"
    assert body["record_count"] == 24

    # Counts aggregate: every column counted once per non-null row. All 24 rows have
    # values for the three projected columns, so each entry is 24. cohort.py replaces
    # underscores in column names with newlines for plot rendering.
    counts = _aggregate(body, "Counts")
    assert counts == {"person\nid": 24, "modality\nconcept\nid": 24, "accession\nid": 24}

    # Sex Distribution: persons 1-12 appear in image_occurrence; 6 M, 6 F. The SQL
    # groups by gender_source_value of distinct person_id. The compose stack runs
    # with COHORT_QUERY_THRESHOLD=5 (see trust/compose.test.yml), so 6 clears the
    # bucket-rollup threshold and neither value collapses into "Other".
    sex = _aggregate(body, "Sex Distribution")
    assert sex == {"M": 6, "F": 6}

    # Age Distribution: 12 distinct person_ids, decade-bucketed. The largest bucket
    # holds 3 patients, all below the threshold of 5, so every bucket rolls into
    # the single "Other" row.
    age = _aggregate(body, "Age Distribution")
    assert age == {"Other": 12}


@pytest.mark.asyncio
async def test_multiple_aggregates_join_returns_all_groups(stub_hub_received):
    """Joining person + image_occurrence + concept still produces every aggregate."""
    query = (
        "SELECT p.person_id, p.gender_source_value, c.concept_name AS modality, io.accession_id "
        "FROM omop.person p "
        "INNER JOIN omop.image_occurrence io ON io.person_id = p.person_id "
        "LEFT JOIN omop.concept c ON c.concept_id = io.modality_concept_id"
    )
    result = await handle_cohort_query(_payload(query, query_id="qid-join"))

    assert result == {"success": True}
    body = json.loads(stub_hub_received[0]["body"])
    assert body["query_id"] == "qid-join"
    assert body["record_count"] == 24  # one row per image_occurrence

    # Both Counts and Nulls groups should be present.
    aggregate_names = {g["name"] for g in body["data"]}
    assert {"Counts", "Nulls", "Sex Distribution", "Age Distribution"}.issubset(aggregate_names)

    # No nulls in any of the projected columns — the join is INNER on person and the
    # LEFT JOIN on concept always finds a hit because every modality_concept_id in the
    # seed has a matching concept row.
    nulls = _aggregate(body, "Nulls")
    assert all(count == 0 for count in nulls.values())


@pytest.mark.asyncio
async def test_realistic_cte_multi_join_shaped_query(stub_hub_received):
    """Mirror the shape of ``flip-api/tests/example_query.sql`` — CTEs + multi-table
    chain + concept-name filter — at small scale.

    The simpler tests above cover the SQL plumbing for a single-table SELECT; this one
    exercises the patterns FLIP users actually write: a procedure-concept CTE, the
    canonical person → visit → procedure → image join, and a ``WHERE`` on
    ``concept_name`` to project a specific cohort. Seed has 6 CT Spleen procedures
    (persons 1-6) and 6 CT Head procedures (persons 7-12) so the filter returns a
    deterministic 6-row result that clears the threshold.
    """
    query = """
        WITH procedure_concept AS (
            SELECT concept_id, concept_name
            FROM omop.concept
            WHERE domain_id = 'Procedure'
        ),
        modality_concept AS (
            SELECT concept_id, concept_name
            FROM omop.concept
            WHERE concept_class_id = 'Modality'
        )
        SELECT
            p.person_id,
            p.gender_source_value,
            pr.procedure_occurrence_id,
            pr.procedure_date,
            proc_c.concept_name AS procedure_name,
            io.accession_id,
            io.image_occurrence_date,
            mod_c.concept_name AS modality
        FROM omop.person p
        LEFT JOIN omop.visit_occurrence v ON v.person_id = p.person_id
        LEFT JOIN omop.procedure_occurrence pr ON pr.visit_occurrence_id = v.visit_occurrence_id
        LEFT JOIN omop.image_occurrence io ON io.procedure_occurrence_id = pr.procedure_occurrence_id
        LEFT JOIN procedure_concept proc_c ON proc_c.concept_id = pr.procedure_concept_id
        LEFT JOIN modality_concept mod_c ON mod_c.concept_id = io.modality_concept_id
        WHERE proc_c.concept_name = 'CT Spleen'
    """
    result = await handle_cohort_query(_payload(query, query_id="qid-realistic"))

    assert result == {"success": True}
    body = json.loads(stub_hub_received[0]["body"])
    assert body["query_id"] == "qid-realistic"
    # Six persons (1-6) had a CT Spleen procedure in the seed; the CTE filter must drop
    # the other six (CT Head) plus the four imaging-less persons (13-16) plus the MR/XR
    # rows whose procedure_occurrence_id is NULL.
    assert body["record_count"] == 6
    aggregate_names = {g["name"] for g in body["data"]}
    assert {"Counts", "Nulls", "Sex Distribution", "Age Distribution"}.issubset(aggregate_names)

    # Counts: every projected column has 6 non-null values (column names get
    # underscores → newlines for chart rendering, see services/cohort.get_counts).
    counts = _aggregate(body, "Counts")
    assert counts.get("accession\nid") == 6
    assert counts.get("procedure\nname") == 6


# ---------------------------------------------------------------------------
# Failure modes — each one asserts the handler reports failure cleanly without
# crashing, and that the stub hub never receives a callback.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_result_below_threshold_returns_failure(stub_hub_received):
    """A query that matches no rows trips the cohort-size threshold and surfaces as failure."""
    result = await handle_cohort_query(
        _payload("SELECT * FROM omop.image_occurrence WHERE accession_id = 'NONEXISTENT'")
    )

    assert result["success"] is False
    # data-access-api raises HTTPException(400, "Query returned too few records: 0 ...");
    # make_request promotes that to an HTTPException whose detail is the upstream body.
    assert "too few records" in result["error"].lower()
    # The hub callback should not fire when the data-access-api hop already failed.
    assert stub_hub_received == []


@pytest.mark.asyncio
async def test_malformed_payload_validation_error(stub_hub_received):
    """Missing required fields in the payload short-circuit before any HTTP call."""
    # No query_name / encrypted_project_id / query_id / trust_id.
    result = await handle_cohort_query({"query": "SELECT 1"})

    assert result["success"] is False
    assert "validation error" in result["error"].lower()
    assert stub_hub_received == []


@pytest.mark.asyncio
async def test_data_access_api_unreachable_returns_failure(monkeypatch, stub_hub_received):
    """Pointing trust-api at a dead port should produce a clean failure envelope."""
    from trust_api.services import task_handlers

    # 127.0.0.1:1 — port 1 is privileged and effectively guaranteed to refuse.
    monkeypatch.setattr(task_handlers, "DATA_ACCESS_API_URL", "http://127.0.0.1:1")

    result = await handle_cohort_query(_payload("SELECT * FROM omop.image_occurrence"))

    assert result["success"] is False
    # ``make_request`` maps connection errors to HTTPException(502, "Failed to connect ...").
    assert "failed to connect" in result["error"].lower() or "502" in result["error"]
    assert stub_hub_received == []


@pytest.mark.asyncio
async def test_sql_injection_attempt_rejected(stub_hub_received):
    """Multi-statement payloads (e.g. trailing ``DROP``) are filtered by
    data-access-api's validate_query."""
    result = await handle_cohort_query(
        _payload("SELECT * FROM omop.image_occurrence; DROP TABLE omop.person")
    )

    assert result["success"] is False
    # The AST-based validate_query enforces a single-statement invariant before any DDL/
    # DML check, so query-stacking surfaces as the "one statement per request" message.
    assert "one sql statement" in result["error"].lower()
    assert stub_hub_received == []


@pytest.mark.asyncio
async def test_missing_table_returns_failure(stub_hub_received):
    """Querying a table that doesn't exist surfaces as a 400 from data-access-api."""
    result = await handle_cohort_query(_payload("SELECT * FROM omop.does_not_exist"))

    assert result["success"] is False
    # Apply ``.lower()`` to both halves of the OR — Postgres versions vary on identifier
    # quoting / casing in error messages, so making both checks case-insensitive avoids
    # a future-Postgres flake.
    err = result["error"].lower()
    assert "does_not_exist" in err or "does not exist" in err
    assert stub_hub_received == []
