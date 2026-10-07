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

"""Who decides a trust's participation in a project, checked against real role rows (FLIP#1258).

A trust with at least one Trust Admin decides for itself: only its Trust Admins may approve or decline there, and
the hub admin may not. A trust with none is decided by the hub admin, as before site approval existed. Every
recorded decision says which of the two made it (``decided_as``).

Real Postgres via the shared session fixture, and real ``user_role`` rows: the checks walk ``user_role`` →
``role_permission`` → ``permission`` with a scope predicate, which a mocked session cannot exercise.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import select

from flip_api.db.models.main_models import ModelTrustIntersect, ProjectTrustIntersect, TrustTask
from flip_api.db.models.user_models import RoleRef, UserRole
from flip_api.domain.interfaces.trust import ITrust
from flip_api.domain.schemas.projects import ApproveProjectBodyPayload
from flip_api.domain.schemas.status import DecisionMaker, ProjectStatus, TrustApprovalStatus, TrustIntersectStatus
from flip_api.project_services.approve_project import approve_project_endpoint
from flip_api.trusts_services.start_project_imaging_creation import start_project_imaging_creation


@pytest.fixture
def staged_project(session, user_factory, project_factory, trust_factory, project_trust_intersect_factory):
    """A STAGED project with two pending intersects — the precondition for a decision."""
    owner = user_factory()
    project = project_factory.build(owner_id=owner.id, status=ProjectStatus.STAGED, deleted=False)
    trusts = [trust_factory.build(), trust_factory.build()]

    session.add(project)
    for trust in trusts:
        session.add(trust)
    session.flush()
    for trust in trusts:
        session.add(
            project_trust_intersect_factory.build(
                project_id=project.id, trust_id=trust.id, status=TrustApprovalStatus.PENDING
            )
        )
    session.commit()

    return {"project": project, "trusts": trusts}


def _payload(trusts, declined=()) -> ApproveProjectBodyPayload:
    return ApproveProjectBodyPayload(trusts=[t.id for t in trusts], declined=[t.id for t in declined])


def _decisions(session, project) -> dict:
    rows = session.exec(
        select(ProjectTrustIntersect)
        .where(ProjectTrustIntersect.project_id == project.id)
        .execution_options(populate_existing=True)
    ).all()
    return {row.trust_id: row for row in rows}


def _grant(user_id, role, trust_id=None):
    return UserRole(user_id=user_id, role_id=role.value, trust_id=trust_id)


def _add(session, *rows):
    for row in rows:
        session.add(row)
    session.commit()


def _refused(session, project, payload, user_id) -> int:
    with pytest.raises(HTTPException) as exc_info:
        approve_project_endpoint(project.id, payload, user_id, session)
    return exc_info.value.status_code


def _all_pending(session, project) -> bool:
    return {row.status for row in _decisions(session, project).values()} == {TrustApprovalStatus.PENDING}


def test_hub_admin_decides_a_trust_with_no_trust_admin(session, staged_project):
    admin_id = uuid4()
    _add(session, _grant(admin_id, RoleRef.ADMIN))
    project = staged_project["project"]
    first, _second = staged_project["trusts"]

    approve_project_endpoint(project.id, _payload([first]), admin_id, session)

    row = _decisions(session, project)[first.id]
    assert (row.status, row.decided_as, row.decided_by) == (TrustApprovalStatus.APPROVED, DecisionMaker.HUB, admin_id)


def test_hub_admin_cannot_decide_a_trust_that_has_a_trust_admin(session, staged_project):
    admin_id, trust_admin = uuid4(), uuid4()
    site_trust, _other = staged_project["trusts"]
    _add(session, _grant(admin_id, RoleRef.ADMIN), _grant(trust_admin, RoleRef.TRUST_ADMIN, trust_id=site_trust.id))
    project = staged_project["project"]

    assert _refused(session, project, _payload([site_trust]), admin_id) == 403
    assert _all_pending(session, project)


def test_trust_admin_decides_their_trust_as_site(session, staged_project):
    trust_admin = uuid4()
    site_trust, _other = staged_project["trusts"]
    _add(session, _grant(trust_admin, RoleRef.TRUST_ADMIN, trust_id=site_trust.id))
    project = staged_project["project"]

    approve_project_endpoint(project.id, _payload([site_trust]), trust_admin, session)

    row = _decisions(session, project)[site_trust.id]
    assert (row.status, row.decided_as, row.decided_by) == (
        TrustApprovalStatus.APPROVED,
        DecisionMaker.SITE,
        trust_admin,
    )


def test_trust_admin_declines_their_trust_as_site(session, staged_project):
    trust_admin = uuid4()
    site_trust, _other = staged_project["trusts"]
    _add(session, _grant(trust_admin, RoleRef.TRUST_ADMIN, trust_id=site_trust.id))
    project = staged_project["project"]

    approve_project_endpoint(project.id, _payload([], declined=[site_trust]), trust_admin, session)

    row = _decisions(session, project)[site_trust.id]
    assert (row.status, row.decided_as) == (TrustApprovalStatus.DECLINED, DecisionMaker.SITE)


def test_trust_admin_cannot_decide_a_hub_run_trust(session, staged_project):
    trust_admin = uuid4()
    site_trust, hub_trust = staged_project["trusts"]
    _add(session, _grant(trust_admin, RoleRef.TRUST_ADMIN, trust_id=site_trust.id))

    assert _refused(session, staged_project["project"], _payload([hub_trust]), trust_admin) == 403


def test_trust_admin_cannot_decide_another_trusts_decision(session, staged_project):
    """Authority at trust A says nothing about trust B, even when B decides for itself too."""
    admin_a, admin_b = uuid4(), uuid4()
    a, b = staged_project["trusts"]
    _add(
        session,
        _grant(admin_a, RoleRef.TRUST_ADMIN, trust_id=a.id),
        _grant(admin_b, RoleRef.TRUST_ADMIN, trust_id=b.id),
    )
    project = staged_project["project"]

    assert _refused(session, project, _payload([], declined=[b]), admin_a) == 403
    assert _all_pending(session, project)


def test_one_trust_the_caller_may_not_decide_refuses_the_whole_call(session, staged_project):
    """All-or-nothing: a call deciding a trust the caller holds no authority over writes nothing at all."""
    trust_admin = uuid4()
    own, other = staged_project["trusts"]
    _add(session, _grant(trust_admin, RoleRef.TRUST_ADMIN, trust_id=own.id))
    project = staged_project["project"]

    assert _refused(session, project, _payload([own, other]), trust_admin) == 403
    assert _all_pending(session, project), "no partial decision may have been committed"


def test_admin_who_is_trust_admin_decides_as_site(session, staged_project):
    """An Admin who also holds Trust Admin at A decides A as SITE and B (no Trust Admin) as HUB, in one call."""
    admin_id = uuid4()
    a, b = staged_project["trusts"]
    _add(session, _grant(admin_id, RoleRef.ADMIN), _grant(admin_id, RoleRef.TRUST_ADMIN, trust_id=a.id))
    project = staged_project["project"]

    approve_project_endpoint(project.id, _payload([a], declined=[b]), admin_id, session)

    decisions = _decisions(session, project)
    assert decisions[a.id].decided_as == DecisionMaker.SITE
    assert decisions[b.id].decided_as == DecisionMaker.HUB


def test_removing_last_trust_admin_hands_decision_back_to_hub(session, staged_project):
    admin_id, trust_admin = uuid4(), uuid4()
    site_trust, _other = staged_project["trusts"]
    grant = _grant(trust_admin, RoleRef.TRUST_ADMIN, trust_id=site_trust.id)
    _add(session, _grant(admin_id, RoleRef.ADMIN), grant)
    project = staged_project["project"]
    assert _refused(session, project, _payload([site_trust]), admin_id) == 403

    session.delete(grant)
    session.commit()
    approve_project_endpoint(project.id, _payload([site_trust]), admin_id, session)

    assert _decisions(session, project)[site_trust.id].decided_as == DecisionMaker.HUB


def test_each_trust_admin_decides_in_their_own_time(session, staged_project):
    """Two sites, two Trust Admins, two calls: the first approval approves the project, the second joins later.

    Each call starts only the trust it approved — the first trust is not re-dispatched when the second joins.
    """
    first, second = staged_project["trusts"]
    first_admin, second_admin = uuid4(), uuid4()
    _add(
        session,
        _grant(first_admin, RoleRef.TRUST_ADMIN, trust_id=first.id),
        _grant(second_admin, RoleRef.TRUST_ADMIN, trust_id=second.id),
    )
    project = staged_project["project"]

    assert [t.id for t in approve_project_endpoint(project.id, _payload([first]), first_admin, session)] == [first.id]
    session.refresh(project)
    assert project.status == ProjectStatus.APPROVED

    assert [t.id for t in approve_project_endpoint(project.id, _payload([second]), second_admin, session)] == [
        second.id
    ]
    decisions = _decisions(session, project)
    assert decisions[first.id].decided_by == first_admin
    assert decisions[second.id].decided_by == second_admin


def test_a_researcher_cannot_decide_a_hub_run_trust(session, staged_project):
    researcher = uuid4()
    _add(session, _grant(researcher, RoleRef.RESEARCHER))
    first, _second = staged_project["trusts"]

    assert _refused(session, staged_project["project"], _payload([first]), researcher) == 403


def test_trust_admin_role_granted_globally_confers_no_per_trust_authority(session, staged_project):
    """``trust_id IS NULL`` never satisfies a trust-scoped check, even for the right role.

    The Trust Admin role carries ``CAN_APPROVE_FOR_TRUST``, so a row naming the role but no trust is the one shape
    that could be misread as "may approve anywhere". It must not be: authority is per trust, and a global row
    carries none. ``set_user_roles`` never writes this shape; this pins the check itself.
    """
    user_id, trust_admin = uuid4(), uuid4()
    site_trust, _other = staged_project["trusts"]
    _add(
        session,
        _grant(user_id, RoleRef.TRUST_ADMIN, trust_id=None),
        _grant(trust_admin, RoleRef.TRUST_ADMIN, trust_id=site_trust.id),
    )

    assert _refused(session, staged_project["project"], _payload([site_trust]), user_id) == 403


def _hub_admin(session):
    admin_id = uuid4()
    _add(session, _grant(admin_id, RoleRef.ADMIN))
    return admin_id


def test_project_is_approved_as_soon_as_one_trust_approves(session, staged_project):
    admin_id = _hub_admin(session)
    project = staged_project["project"]
    first, second = staged_project["trusts"]

    activated = approve_project_endpoint(project.id, _payload([first]), admin_id, session)

    assert [t.id for t in activated] == [first.id]
    session.refresh(project)
    assert project.status == ProjectStatus.APPROVED
    assert _decisions(session, project)[second.id].status == TrustApprovalStatus.PENDING


def test_a_declined_trust_alone_leaves_the_project_staged(session, staged_project):
    admin_id = _hub_admin(session)
    project = staged_project["project"]
    first, _second = staged_project["trusts"]

    assert approve_project_endpoint(project.id, _payload([], declined=[first]), admin_id, session) == []

    session.refresh(project)
    assert project.status == ProjectStatus.STAGED


def test_late_approval_activates_only_the_new_trust(session, staged_project):
    admin_id = _hub_admin(session)
    project = staged_project["project"]
    first, second = staged_project["trusts"]
    approve_project_endpoint(project.id, _payload([first]), admin_id, session)

    activated = approve_project_endpoint(project.id, _payload([second]), admin_id, session)

    assert [t.id for t in activated] == [second.id]


def test_late_decline_is_recorded_and_activates_nothing(session, staged_project):
    admin_id = _hub_admin(session)
    project = staged_project["project"]
    first, second = staged_project["trusts"]
    approve_project_endpoint(project.id, _payload([first]), admin_id, session)

    assert approve_project_endpoint(project.id, _payload([], declined=[second]), admin_id, session) == []
    assert _decisions(session, project)[second.id].status == TrustApprovalStatus.DECLINED


def test_resent_approval_after_project_approved_activates_nothing(session, staged_project):
    """Re-sending an approval the trust already made starts nothing: no second imaging pull."""
    admin_id = _hub_admin(session)
    project = staged_project["project"]
    first, _second = staged_project["trusts"]
    approve_project_endpoint(project.id, _payload([first]), admin_id, session)

    assert approve_project_endpoint(project.id, _payload([first]), admin_id, session) == []


def test_a_final_decision_cannot_change_once_the_project_is_approved(session, staged_project):
    admin_id = _hub_admin(session)
    project = staged_project["project"]
    first, _second = staged_project["trusts"]
    approve_project_endpoint(project.id, _payload([first]), admin_id, session)

    assert _refused(session, project, _payload([], declined=[first]), admin_id) == 400
    assert _decisions(session, project)[first.id].status == TrustApprovalStatus.APPROVED


def test_late_trust_joins_existing_models(session, staged_project, model_factory):
    admin_id = _hub_admin(session)
    project = staged_project["project"]
    first, second = staged_project["trusts"]
    approve_project_endpoint(project.id, _payload([first]), admin_id, session)
    # A model created after approval is linked by save_model to the trusts approved then — `first` only.
    model = model_factory.build(project_id=project.id, deleted=False)
    session.add(model)
    session.flush()
    session.add(ModelTrustIntersect(model_id=model.id, trust_id=first.id, status=TrustIntersectStatus.PENDING))
    session.commit()

    approve_project_endpoint(project.id, _payload([second]), admin_id, session)

    links = session.exec(select(ModelTrustIntersect).where(ModelTrustIntersect.model_id == model.id)).all()
    assert {link.trust_id: link.status for link in links} == {
        first.id: TrustIntersectStatus.PENDING,
        second.id: TrustIntersectStatus.PENDING,
    }


def test_decisions_on_an_unstaged_project_are_refused(session, staged_project):
    admin_id = _hub_admin(session)
    project = staged_project["project"]
    project.status = ProjectStatus.UNSTAGED
    session.add(project)
    session.commit()

    assert _refused(session, project, _payload([staged_project["trusts"][0]]), admin_id) == 400


@pytest.fixture
def no_directory_users(fake_idp):
    """The imaging route lists the project's users from the identity provider; none are needed to test who may
    start it."""
    fake_idp.list_users.return_value = []
    return fake_idp


async def _start_imaging(session, project, trust, user_id, idp):
    return await start_project_imaging_creation(
        request=MagicMock(),
        project_id=project.id,
        trust=ITrust(id=trust.id, name=trust.name),
        db=session,
        user_id=user_id,
        idp=idp,
    )


def _imaging_tasks(session, trust) -> list[TrustTask]:
    return list(session.exec(select(TrustTask).where(TrustTask.trust_id == trust.id)).all())


@pytest.mark.asyncio
async def test_imaging_cannot_start_before_the_project_is_approved(session, staged_project, no_directory_users):
    admin_id = uuid4()
    _add(session, _grant(admin_id, RoleRef.ADMIN))
    first, _second = staged_project["trusts"]

    with pytest.raises(HTTPException) as exc_info:
        await _start_imaging(session, staged_project["project"], first, admin_id, no_directory_users)

    assert exc_info.value.status_code == 409
    assert _imaging_tasks(session, first) == []


@pytest.mark.asyncio
async def test_imaging_cannot_start_at_a_trust_that_declined(session, staged_project, no_directory_users):
    admin_id = uuid4()
    _add(session, _grant(admin_id, RoleRef.ADMIN))
    project = staged_project["project"]
    first, second = staged_project["trusts"]
    approve_project_endpoint(project.id, _payload([first], declined=[second]), admin_id, session)

    with pytest.raises(HTTPException) as exc_info:
        await _start_imaging(session, project, second, admin_id, no_directory_users)

    assert exc_info.value.status_code == 409
    assert _imaging_tasks(session, second) == []


@pytest.mark.asyncio
async def test_imaging_starts_at_a_trust_that_approved(session, staged_project, no_directory_users):
    admin_id = uuid4()
    _add(session, _grant(admin_id, RoleRef.ADMIN))
    project = staged_project["project"]
    first, _second = staged_project["trusts"]
    approve_project_endpoint(project.id, _payload([first]), admin_id, session)

    await _start_imaging(session, project, first, admin_id, no_directory_users)

    assert len(_imaging_tasks(session, first)) == 1
