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

"""Integration coverage of ``submit_cohort_query``'s project scoping, against real Postgres.

The security property is that a caller authorised on project A cannot dispatch — or mutate — a
``Queries`` row belonging to project B. A mocked session cannot prove that: it returns whatever the
test stubs regardless of whether the statement filters on ``project_id``. Only a real database shows
that B's row is untouched and that no ``TrustTask`` rows were created.
"""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from flip_api.cohort_services.submit_cohort_query import submit_cohort_query
from flip_api.db.models.main_models import Queries, TrustTask
from flip_api.domain.schemas.cohort import SubmitCohortQuery
from flip_api.domain.schemas.status import ProjectStatus
from tests.fixtures.user_fixtures import mock_request

__all__ = ["mock_request"]

ATTACKER_SQL = "SELECT person_id FROM omop.person"
VICTIM_SQL = "SELECT person_id FROM omop.observation"


@pytest.fixture
def attacker(session: Session, project_factory):
    """A project the caller owns — ``can_modify_project`` passes on ownership alone."""
    owner_id = uuid4()
    project = project_factory.build(owner_id=owner_id, deleted=False, status=ProjectStatus.UNSTAGED)
    session.add(project)
    session.commit()
    query = Queries(
        id=uuid4(), project_id=project.id, name="mine", query=ATTACKER_SQL,
        created_by=owner_id, queried_trust_ids=[],
    )
    session.add(query)
    session.commit()

    return owner_id, project, query


@pytest.fixture
def victim_query(session: Session, project_factory):
    """A ``Queries`` row belonging to a project the caller has nothing to do with."""
    project = project_factory.build(owner_id=uuid4(), deleted=False, status=ProjectStatus.UNSTAGED)
    session.add(project)
    session.commit()
    query = Queries(
        id=uuid4(), project_id=project.id, name="theirs", query=VICTIM_SQL,
        created_by=project.owner_id, queried_trust_ids=[],
    )
    session.add(query)
    session.commit()

    return query


def test_cannot_dispatch_another_projects_query(
    session: Session, mock_request, attacker, victim_query, trust_factory
):
    """Authorised on project A, supplying project B's query_id → refused, and B is untouched.

    The 404 alone is not the property worth pinning. What matters is that nothing was dispatched
    and B's row was not mutated — previously the fan-out ran and B's ``queried_trust_ids`` was
    overwritten, which is what corrupted the results B's members then read.
    """
    owner_id, project, _ = attacker
    # A Trust must exist, otherwise submit 404s at the trust listing instead of at the scoped
    # lookup — and every assertion below would then hold for the wrong reason. `trust` is in
    # conftest's _PRESERVED_TABLES, so relying on rows left by other test files would also make
    # this order-dependent.
    session.add(trust_factory.build())
    session.commit()

    payload = SubmitCohortQuery(
        name="poison",
        query=ATTACKER_SQL,
        project_id=project.id,
        query_id=victim_query.id,
        authenticationToken="Bearer test-token",
    )

    with pytest.raises(HTTPException) as exc_info:
        submit_cohort_query(mock_request, payload, session, owner_id)

    assert exc_info.value.status_code == 404
    # Pin the *reason*: "No trusts found" is also a 404 from this endpoint.
    assert exc_info.value.detail == "Cohort query not found for this project."

    session.expire_all()
    untouched = session.get(Queries, victim_query.id)
    assert untouched is not None
    assert untouched.queried_trust_ids == []
    assert untouched.query == VICTIM_SQL
    assert session.exec(select(TrustTask)).all() == []


def test_dispatches_a_same_project_query_and_uses_the_persisted_sql(
    session: Session, mock_request, attacker, trust_factory
):
    """Positive control: the caller's own query_id on their own project dispatches.

    Without this the scoping is only pinned against *under*-scoping. An *over*-scoped predicate —
    one that compares the wrong columns, e.g. ``Queries.project_id == cohort_query.query_id`` —
    would 404 every legitimate submit while the negative test and the mocked unit tests all stay
    green (they never exercise a real same-project lookup). This also pins, at the DB level, that
    the SQL shipped to the trust is the persisted row's, not the request body's.
    """
    owner_id, project, query = attacker
    trust = trust_factory.build()
    session.add(trust)
    session.commit()

    # A body query that is neither a substring nor a superstring of the persisted SQL, so the
    # payload assertions below can only pass if the persisted row — not the body — was dispatched.
    divergent_body_sql = "SELECT 1 AS one"
    payload = SubmitCohortQuery(
        name="mine",
        query=divergent_body_sql,
        project_id=project.id,
        query_id=query.id,
        authenticationToken="Bearer test-token",
    )

    result = submit_cohort_query(mock_request, payload, session, owner_id)

    # Dispatch happened — an over-scoped lookup would have 404'd before reaching here.
    assert result.query_id == query.id

    # `trust` is in conftest's _PRESERVED_TABLES, so other files' rows persist and the submit fans
    # out to all of them. Filter by this query's id — those tasks are unambiguously ours.
    tasks = session.exec(select(TrustTask).where(TrustTask.query_id == query.id)).all()
    assert len(tasks) >= 1
    assert trust.id in {t.trust_id for t in tasks}
    for task in tasks:
        # The persisted SQL is dispatched, never the divergent request body.
        assert ATTACKER_SQL in task.payload
        assert divergent_body_sql not in task.payload

    session.expire_all()
    refreshed = session.get(Queries, query.id)
    assert refreshed is not None
    assert {str(t) for t in refreshed.queried_trust_ids} == {str(task.trust_id) for task in tasks}
