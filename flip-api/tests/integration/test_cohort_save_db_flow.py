# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Integration coverage of the cohort-results save path against real Postgres.

Replaces the mock-everything cohort tests that previously lived as
``test_receive_cohort_results.py`` (they hit a ``real_client`` against an
already-running flip-api, which never ran in CI). These exercise the
private_services internals directly against the throwaway DB so the
serialization to ``query_result.data`` and the JOIN-driven aggregation are
both exercised.
"""

import json
import uuid

import pytest
from sqlmodel import select

from flip_api.db.models.main_models import Queries, QueryResult, QueryStats, Trust
from flip_api.domain.schemas.private import OmopCohortResults, OmopData, Results
from flip_api.private_services.receive_cohort_results import (
    _aggregate_and_save_results,
    _save_individual_result,
)


@pytest.fixture
def trust_a(session) -> Trust:
    trust = Trust(name="Trust_A")
    session.add(trust)
    session.commit()
    session.refresh(trust)
    return trust


@pytest.fixture
def trust_b(session) -> Trust:
    trust = Trust(name="Trust_B")
    session.add(trust)
    session.commit()
    session.refresh(trust)
    return trust


@pytest.fixture
def seeded_query(session) -> Queries:
    """Insert a Queries row so query_result.query_id FK is satisfied."""
    q = Queries(name="cohort-test", query="SELECT 1")
    session.add(q)
    session.commit()
    session.refresh(q)
    return q


def _cohort_results(query_id: uuid.UUID, trust_id: uuid.UUID, count: int) -> OmopCohortResults:
    half = count // 2
    return OmopCohortResults(
        query_id=query_id,
        trust_id=trust_id,
        created="2026-05-01T12:00:00Z",
        record_count=count,
        data=[
            OmopData(
                name="age_group",
                results=[
                    Results(value="<50", count=half),
                    Results(value=">=50", count=count - half),
                ],
            ),
        ],
    )


def test_save_individual_persists_serialized_payload(session, trust_a, seeded_query):
    """The first save should INSERT a query_result row with the JSON payload."""
    query_id = seeded_query.id
    payload = _cohort_results(query_id, trust_a.id, count=10)

    _save_individual_result(session, payload)

    persisted = session.exec(
        select(QueryResult).where(QueryResult.query_id == query_id, QueryResult.trust_id == trust_a.id)
    ).first()
    assert persisted is not None, "QueryResult must be inserted on first save"

    parsed = json.loads(persisted.data)
    assert parsed["record_count"] == 10
    assert parsed["data"][0]["name"] == "age_group"
    assert {r["value"] for r in parsed["data"][0]["results"]} == {"<50", ">=50"}


def test_save_individual_updates_existing_row_on_resubmit(session, trust_a, seeded_query):
    """A resubmit for the same (query_id, trust_id) must UPDATE, not duplicate."""
    query_id = seeded_query.id
    _save_individual_result(session, _cohort_results(query_id, trust_a.id, count=10))
    _save_individual_result(session, _cohort_results(query_id, trust_a.id, count=42))

    rows = session.exec(
        select(QueryResult).where(QueryResult.query_id == query_id, QueryResult.trust_id == trust_a.id)
    ).all()
    assert len(rows) == 1, "Resubmit must not insert a duplicate row"
    assert json.loads(rows[0].data)["record_count"] == 42


def test_aggregate_persists_total_record_count_across_trusts(session, trust_a, trust_b, seeded_query):
    """Aggregation across two trusts should sum record counts and persist one QueryStats row."""
    query_id = seeded_query.id
    _save_individual_result(session, _cohort_results(query_id, trust_a.id, count=8))
    _save_individual_result(session, _cohort_results(query_id, trust_b.id, count=5))

    _aggregate_and_save_results(session, query_id)

    stats_row = session.exec(select(QueryStats).where(QueryStats.query_id == query_id)).first()
    assert stats_row is not None, "Aggregation must persist exactly one QueryStats row"

    aggregated = json.loads(stats_row.stats)
    assert aggregated["record_count"] == 13
    assert {tr["trust_name"] for r in aggregated["trusts_results"] for tr in r["results"]} == {
        "Trust_A",
        "Trust_B",
    }


def test_aggregate_idempotent_on_resubmit(session, trust_a, seeded_query):
    """Re-running aggregation must update the existing QueryStats row in place."""
    query_id = seeded_query.id
    _save_individual_result(session, _cohort_results(query_id, trust_a.id, count=4))
    _aggregate_and_save_results(session, query_id)

    _save_individual_result(session, _cohort_results(query_id, trust_a.id, count=20))
    _aggregate_and_save_results(session, query_id)

    rows = session.exec(select(QueryStats).where(QueryStats.query_id == query_id)).all()
    assert len(rows) == 1, "Re-aggregation must update in place, not insert a second stats row"
    assert json.loads(rows[0].stats)["record_count"] == 20
