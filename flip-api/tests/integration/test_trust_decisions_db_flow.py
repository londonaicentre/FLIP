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

"""GET /trust/{trust_id}/decisions — the My Trust page's list, against real rows (FLIP#1258)."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from flip_api.db.models.main_models import ProjectsAudit, Queries, QueryResult
from flip_api.db.models.user_models import RoleRef, UserProfile, UserRole
from flip_api.domain.schemas.actions import ProjectAuditAction
from flip_api.domain.schemas.status import DecisionMaker, ProjectStatus, TrustApprovalStatus
from flip_api.trusts_services.get_trust_decisions import get_trust_decisions


@pytest.fixture
def world(session, project_factory, trust_factory, project_trust_intersect_factory):
    """Trust T (Trust Admin ``admin``) and U; P1 pending at T and U, P2 approved at T by its admin, P3 deleted."""
    admin, owner = uuid4(), uuid4()
    t, u = trust_factory.build(), trust_factory.build()
    p1 = project_factory.build(owner_id=owner, status=ProjectStatus.STAGED, deleted=False, has_imaging=True)
    p2 = project_factory.build(owner_id=owner, status=ProjectStatus.APPROVED, deleted=False, has_imaging=False)
    p3 = project_factory.build(owner_id=owner, status=ProjectStatus.STAGED, deleted=True)
    for row in (t, u, p1, p2, p3, UserProfile(user_id=owner, name="Olive Owner", organisation="KCL")):
        session.add(row)
    session.add(UserProfile(user_id=admin, name="Ada Admin", organisation="T"))
    session.flush()
    decided_at = datetime(2026, 9, 1, 12, 0, 0)
    for project, trust, status, by, at, as_ in [
        (p1, t, TrustApprovalStatus.PENDING, None, None, None),
        (p1, u, TrustApprovalStatus.PENDING, None, None, None),
        (p2, t, TrustApprovalStatus.APPROVED, admin, decided_at, DecisionMaker.SITE),
        (p3, t, TrustApprovalStatus.PENDING, None, None, None),
    ]:
        session.add(
            project_trust_intersect_factory.build(
                project_id=project.id, trust_id=trust.id, status=status, decided_by=by, decided_at=at, decided_as=as_
            )
        )
    query = Queries(
        name="cohort",
        query="SELECT person_id FROM omop.person LIMIT 10",
        created_by=owner,
        project_id=p1.id,
        queried_trust_ids=[t.id, u.id],
    )
    session.add(query)
    session.flush()
    session.add(
        QueryResult(
            query_id=query.id,
            trust_id=t.id,
            data=json.dumps({"record_count": 142, "data": [], "error": None, "suppressed": False}),
        )
    )
    session.add(
        ProjectsAudit(
            project_id=p1.id,
            action=ProjectAuditAction.STAGE,
            user_id=owner,
            audit_date=datetime.now(timezone.utc) - timedelta(days=3),
        )
    )
    session.add(UserRole(user_id=admin, role_id=RoleRef.TRUST_ADMIN.value, trust_id=t.id))
    session.commit()
    return {"admin": admin, "t": t, "u": u, "p1": p1, "p2": p2, "p3": p3}


def test_a_trust_admin_sees_their_trusts_projects_pending_first(session, world):
    items = get_trust_decisions(world["t"].id, session, world["admin"])

    assert [i.project_id for i in items] == [world["p1"].id, world["p2"].id]  # the deleted P3 is absent
    pending, decided = items
    assert (pending.status, pending.owner_name, pending.has_imaging) == (
        TrustApprovalStatus.PENDING,
        "Olive Owner",
        True,
    )
    assert pending.query == "SELECT person_id FROM omop.person LIMIT 10"
    assert pending.cohort is not None
    assert pending.cohort.record_count == 142
    assert pending.staged_at is not None
    assert (decided.status, decided.decided_as, decided.decided_by_name) == (
        TrustApprovalStatus.APPROVED,
        DecisionMaker.SITE,
        "Ada Admin",
    )
    assert decided.cohort is None  # P2 has no query at all


def test_the_cohort_is_not_reported_when_the_trust_never_answered(session, world):
    """U's Trust Admin sees P1 without a count: the query ran, but U never returned a result."""
    u_admin = uuid4()
    session.add(UserRole(user_id=u_admin, role_id=RoleRef.TRUST_ADMIN.value, trust_id=world["u"].id))
    session.commit()

    [item] = get_trust_decisions(world["u"].id, session, u_admin)

    assert item.query is not None
    assert item.cohort is None


@pytest.mark.parametrize("role", [RoleRef.ADMIN, RoleRef.RESEARCHER])
def test_only_that_trusts_admin_may_list_it(session, world, role):
    caller = uuid4()
    session.add(UserRole(user_id=caller, role_id=role.value))
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        get_trust_decisions(world["t"].id, session, caller)

    assert exc_info.value.status_code == 403


def test_a_trust_admin_cannot_list_another_trust(session, world):
    with pytest.raises(HTTPException) as exc_info:
        get_trust_decisions(world["u"].id, session, world["admin"])

    assert exc_info.value.status_code == 403


def test_an_unknown_trust_is_404(session, world):
    with pytest.raises(HTTPException) as exc_info:
        get_trust_decisions(uuid4(), session, world["admin"])

    assert exc_info.value.status_code == 404
