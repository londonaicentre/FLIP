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

"""HTTP-level integration tests for the receive-cohort-results endpoint.

Drives the endpoint through ``TestClient`` so the FastAPI dependency graph is
real (auth, session, validation) but no out-of-process service is needed.
``authenticate_trust`` is overridden to return a ``Trust`` row the test owns —
identity is the key, and the endpoint reads ``authenticated_trust.id`` directly.

Pure-DB cohort save flows are in ``test_cohort_save_db_flow.py``.
"""

import json
import uuid

import pytest
from fastapi import HTTPException, status
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool
from sqlmodel import Session, select

from flip_api.auth.access_manager import authenticate_trust
from flip_api.db.models.main_models import Projects, Queries, QueryResult, QueryStats, Trust, TrustTask
from flip_api.domain.schemas.status import ProjectStatus, TaskStatus, TaskType
from flip_api.main import app
from flip_api.private_services.receive_cohort_results import _aggregate_and_save_results


def _dispatch(session: Session, trust: Trust, query_row: Queries, task_status: TaskStatus) -> None:
    """The ``cohort_query`` task submit creates for ``(query, trust)``, in the state the poll leaves it."""
    session.add(
        TrustTask(
            trust_id=trust.id, task_type=TaskType.COHORT_QUERY, query_id=query_row.id, payload="{}", status=task_status
        )
    )
    session.commit()


@pytest.fixture
def sample_cohort_dict():
    return {
        "query_id": str(uuid.uuid4()),
        "trust_id": str(uuid.uuid4()),
        "created": "2026-05-01T12:00:00Z",
        "record_count": 10,
        "data": [
            {
                "name": "age_group",
                "results": [
                    {"value": "<50", "count": 5},
                    {"value": ">=50", "count": 5},
                ],
            }
        ],
    }


class TestReceiveCohortResultsEndpoint:
    def test_bad_payload_returns_422(self, client):
        # Auth must pass so the request reaches body validation (a failing
        # auth dependency would 401 before the 422 ever fires).
        app.dependency_overrides[authenticate_trust] = lambda: Trust(id=uuid.uuid4(), name="Authed Trust")
        try:
            response = client.post(
                "/api/cohort/results",
                json={"trust_id": "abc", "record_count": "ten", "data": []},
            )
            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        finally:
            app.dependency_overrides.pop(authenticate_trust, None)

    def test_unauthorised_trust_id_returns_403(self, client, sample_cohort_dict: dict):
        """The API key resolves to one trust; the payload claims another ⇒ 403."""
        authed = Trust(id=uuid.uuid4(), name="Authed Trust")
        app.dependency_overrides[authenticate_trust] = lambda: authed
        try:
            # sample_cohort_dict.trust_id is an unrelated random uuid.
            assert sample_cohort_dict["trust_id"] != str(authed.id)
            response = client.post("/api/cohort/results", json=sample_cohort_dict)
            assert response.status_code == status.HTTP_403_FORBIDDEN
            assert "not authorised" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.pop(authenticate_trust, None)

    @staticmethod
    def _trust_and_query(session, project: Projects | None = None) -> tuple[Trust, Queries]:
        trust = Trust(name="Round Trip Trust")
        session.add(trust)
        if project is not None:
            session.add(project)
        session.commit()
        session.refresh(trust)

        query_row = Queries(
            name="cohort-endpoint-roundtrip",
            query="SELECT 1",
            created_by=uuid.uuid4(),
            project_id=project.id if project is not None else None,
        )
        session.add(query_row)
        session.commit()
        session.refresh(query_row)
        return trust, query_row

    def test_save_then_aggregate_round_trip(self, client, session, sample_cohort_dict: dict):
        """Full happy path: the authenticated trust was dispatched the query and is mid-task."""
        trust, query_row = self._trust_and_query(session)
        _dispatch(session, trust, query_row, TaskStatus.IN_PROGRESS)

        app.dependency_overrides[authenticate_trust] = lambda: trust
        try:
            sample_cohort_dict["trust_id"] = str(trust.id)
            sample_cohort_dict["query_id"] = str(query_row.id)

            response = client.post("/api/cohort/results", json=sample_cohort_dict)
            assert response.status_code == status.HTTP_200_OK, response.text
            assert response.json() == {"message": "Cohort results processed successfully"}
        finally:
            app.dependency_overrides.pop(authenticate_trust, None)

        assert session.exec(select(QueryResult).where(QueryResult.query_id == query_row.id)).one().trust_id == trust.id
        assert session.exec(select(QueryStats).where(QueryStats.query_id == query_row.id)).one() is not None

    @pytest.mark.parametrize("task_status", [None, TaskStatus.PENDING, TaskStatus.CANCELLED])
    def test_result_for_a_query_not_dispatched_to_the_trust_writes_nothing(
        self, client, session, sample_cohort_dict: dict, task_status
    ):
        """No task, or one the trust was never handed (PENDING) or that was withdrawn (CANCELLED) ⇒ 403,
        and neither a QueryResult nor a QueryStats row appears."""
        trust, query_row = self._trust_and_query(session)
        if task_status is not None:
            _dispatch(session, trust, query_row, task_status)

        app.dependency_overrides[authenticate_trust] = lambda: trust
        try:
            sample_cohort_dict["trust_id"] = str(trust.id)
            sample_cohort_dict["query_id"] = str(query_row.id)

            response = client.post("/api/cohort/results", json=sample_cohort_dict)
            assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
        finally:
            app.dependency_overrides.pop(authenticate_trust, None)

        assert session.exec(select(QueryResult)).all() == []
        assert session.exec(select(QueryStats)).all() == []

    @pytest.mark.parametrize("project_status", [ProjectStatus.STAGED, ProjectStatus.APPROVED])
    def test_result_for_a_staged_or_approved_project_writes_nothing(
        self, client, session, sample_cohort_dict: dict, project_status
    ):
        """A dispatched trust's late result must not change the cohort of record once the project is frozen."""
        project = Projects(name="frozen", description="frozen", owner_id=uuid.uuid4(), status=project_status)
        trust, query_row = self._trust_and_query(session, project)
        _dispatch(session, trust, query_row, TaskStatus.IN_PROGRESS)

        app.dependency_overrides[authenticate_trust] = lambda: trust
        try:
            sample_cohort_dict["trust_id"] = str(trust.id)
            sample_cohort_dict["query_id"] = str(query_row.id)

            response = client.post("/api/cohort/results", json=sample_cohort_dict)
            assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
            assert "staged/approved" in response.json()["detail"]
        finally:
            app.dependency_overrides.pop(authenticate_trust, None)

        assert session.exec(select(QueryResult)).all() == []


class TestAggregateConcurrency:
    """Regression for #579 — aggregation must serialize per query.

    ``_aggregate_and_save_results`` does a read-modify-write of the single QueryStats
    row (read all QueryResult rows → recompute → upsert). When two trusts POST at once
    the passes race and a stale one can clobber a fuller one, dropping a responded trust.
    The fix takes a blocking ``FOR UPDATE`` lock on the parent Queries row first, so the
    aggregations run one-at-a-time.
    """

    def test_aggregate_blocks_on_concurrent_query_row_lock(self, session, pg_container):
        """While another connection holds the parent Queries row, aggregation must block
        and (with a short ``lock_timeout``) error — proving it acquires the serialization
        lock. The holder takes ``FOR KEY SHARE`` on purpose: that is compatible with the
        FK key-share lock the QueryStats insert already needs (so the *pre-fix* code does
        NOT block here), and conflicts only with the ``FOR UPDATE`` the fix adds.

        The integration ``session`` engine shares a single connection (``StaticPool``),
        which can't represent two concurrent transactions, so this test opens its own
        ``NullPool`` engine on the same container to get genuinely separate connections.
        """
        trust = Trust(name="Race Trust")
        session.add(trust)
        session.commit()
        session.refresh(trust)

        query_row = Queries(name="race-query", query="SELECT 1", created_by=uuid.uuid4())
        session.add(query_row)
        session.commit()
        session.refresh(query_row)

        # One committed result so aggregation runs its full read-modify-write path.
        session.add(
            QueryResult(
                query_id=query_row.id,
                trust_id=trust.id,
                data=json.dumps({"record_count": 5, "data": [], "error": None, "suppressed": False}),
            )
        )
        session.commit()

        qid = query_row.id

        concurrent_engine = create_engine(pg_container.get_connection_url(), poolclass=NullPool)
        try:
            with concurrent_engine.connect() as holder:
                # Hold the Queries row so the fix's FOR UPDATE must wait for it.
                holder.execute(text("SELECT id FROM queries WHERE id = :id FOR KEY SHARE"), {"id": str(qid)})

                agg_session = Session(concurrent_engine)
                try:
                    agg_session.execute(text("SET lock_timeout = '1000ms'"))
                    with pytest.raises(HTTPException):
                        _aggregate_and_save_results(agg_session, qid)
                finally:
                    agg_session.rollback()
                    agg_session.close()

                holder.rollback()  # release the lock
        finally:
            concurrent_engine.dispose()


class TestQueryStatsConstraints:
    """QueryStats holds exactly one aggregate row per query (#579 defense-in-depth)."""

    def test_query_id_is_unique(self, session):
        """A second QueryStats row for the same query must be rejected by the DB, not
        silently duplicated — a duplicate would let the read path (`.first()`) pick an
        arbitrary/stale row. This backs up the serialization lock against any future
        non-serialized writer.
        """
        query_row = Queries(name="dup-stats", query="SELECT 1", created_by=uuid.uuid4())
        session.add(query_row)
        session.commit()
        session.refresh(query_row)

        session.add(QueryStats(query_id=query_row.id, stats="{}"))
        session.commit()

        session.add(QueryStats(query_id=query_row.id, stats="{}"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
