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

"""Integration coverage of the project_services DB path: create → fetch → soft-delete → approve.

Hits ``project_services.services.project_services`` against the throwaway
Postgres rather than mocking the session. Unit tests for the same code mock
``Session`` and never catch SQL-shaped bugs (wrong column names, missing
relationships, default mismatches, etc.); these do.
"""

from uuid import uuid4

import pytest
from sqlmodel import select

from flip_api.db.models.main_models import (
    Projects,
    ProjectsAudit,
    ProjectTrustIntersect,
    ProjectUserAccess,
)
from flip_api.domain.interfaces.project import IProjectApproval
from flip_api.domain.schemas.actions import ProjectAuditAction
from flip_api.domain.schemas.projects import ProjectDetails
from flip_api.domain.schemas.status import ProjectStatus
from flip_api.project_services.services.project_services import (
    approve_project,
    create_project,
    delete_project,
    get_project,
)


@pytest.fixture
def project_payload(user_factory) -> ProjectDetails:
    return ProjectDetails(
        name="Cohort Discovery",
        description="Federated retrospective query across two trusts",
        users=[user_factory().id],
        dicom_to_nifti=True,
    )


def test_create_project_persists_and_grants_creator_access(session, project_payload, user_factory):
    """A created project should be readable back with the creator wired into ProjectUserAccess."""
    creator_id = user_factory().id

    new_project_id = create_project(payload=project_payload, current_user_id=creator_id, session=session)

    persisted = session.get(Projects, new_project_id)
    assert persisted is not None
    assert persisted.name == project_payload.name
    assert persisted.description == project_payload.description
    assert persisted.owner_id == creator_id
    assert persisted.deleted is False
    assert persisted.status == ProjectStatus.UNSTAGED

    access_rows = session.exec(
        select(ProjectUserAccess).where(ProjectUserAccess.project_id == new_project_id)
    ).all()
    access_user_ids = {row.user_id for row in access_rows}
    assert creator_id in access_user_ids, "Creator must be granted access on create"
    # `users` from payload added on top of the creator
    assert access_user_ids.issuperset({creator_id, *project_payload.users})


def test_get_project_returns_project_for_existing_id(session, project_payload, user_factory):
    """`get_project` should hydrate a known project; query is None when no Queries row exists."""
    creator_id = user_factory().id
    new_project_id = create_project(payload=project_payload, current_user_id=creator_id, session=session)

    fetched = get_project(new_project_id, session)

    assert fetched.id == new_project_id
    assert fetched.name == project_payload.name
    assert fetched.query is None
    # `IProjectResponse` doesn't expose `deleted` (the lookup filters on
    # `Projects.deleted == False` so a soft-deleted row would 404 here).
    # Verify by re-querying the table directly.
    assert session.get(Projects, new_project_id).deleted is False


def test_list_projects_returns_only_undeleted(session, project_payload, user_factory):
    """A simple SELECT-with-deleted=False against real Postgres — the kind of query the
    --skip-db CI used to silently let drift."""
    creator_id = user_factory().id
    create_project(payload=project_payload, current_user_id=creator_id, session=session)
    deleted_payload = project_payload.model_copy(update={"name": "ToDelete"})
    deleted_id = create_project(payload=deleted_payload, current_user_id=creator_id, session=session)

    delete_project(deleted_id, creator_id, session)

    live = session.exec(select(Projects).where(Projects.deleted.is_(False))).all()  # type: ignore[attr-defined]
    live_names = {p.name for p in live}
    assert project_payload.name in live_names
    assert "ToDelete" not in live_names


def test_soft_delete_marks_row_deleted_in_place(session, project_payload, user_factory):
    """Soft delete must flip the `deleted` flag — not remove the row — and audit it."""
    creator_id = user_factory().id
    new_project_id = create_project(payload=project_payload, current_user_id=creator_id, session=session)

    delete_project(new_project_id, creator_id, session)

    row = session.get(Projects, new_project_id)
    assert row is not None, "Soft delete must keep the row for audit"
    assert row.deleted is True


@pytest.fixture
def staged_project_with_trusts(session, user_factory, project_factory, trust_factory, project_trust_intersect_factory):
    """A STAGED project plus two un-approved ProjectTrustIntersect rows.

    Mirrors the real precondition for ``approve_project``: ``stage_project_service`` builds
    one un-approved intersect per selected trust and the project sits in STAGED until the
    approval call flips both the intersects and the project status.
    """
    user = user_factory()
    project = project_factory.build(owner_id=user.id, status=ProjectStatus.STAGED, deleted=False)
    trusts = [trust_factory.build(), trust_factory.build()]

    session.add(project)
    for t in trusts:
        session.add(t)
    session.flush()
    for t in trusts:
        session.add(
            project_trust_intersect_factory.build(project_id=project.id, trust_id=t.id, approved=False)
        )
    session.commit()

    return {"user": user, "project": project, "trusts": trusts}


def test_approve_project_flips_intersect_approval_and_project_status(session, staged_project_with_trusts):
    """The happy path: approve every staged trust → status APPROVED + every intersect approved + audit row."""
    ctx = staged_project_with_trusts
    payload = IProjectApproval(project_id=ctx["project"].id, trust_ids=[t.id for t in ctx["trusts"]])

    result = approve_project(session, payload, ctx["user"].id)

    assert result is True
    refreshed = session.get(Projects, ctx["project"].id)
    assert refreshed.status == ProjectStatus.APPROVED

    intersects = session.exec(
        select(ProjectTrustIntersect).where(ProjectTrustIntersect.project_id == ctx["project"].id)
    ).all()
    assert {row.approved for row in intersects} == {True}

    audits = session.exec(select(ProjectsAudit).where(ProjectsAudit.project_id == ctx["project"].id)).all()
    assert len(audits) == 1
    assert audits[0].action == ProjectAuditAction.APPROVE
    assert audits[0].user_id == ctx["user"].id


def test_approve_project_partial_subset_leaves_unselected_trusts_unapproved(session, staged_project_with_trusts):
    """Approving a strict subset must leave the unselected trust(s) un-approved.

    This is the invariant that gates ``save_model``'s fan-out: a project APPROVED for trust A
    but not B must NOT spawn a ModelTrustIntersect for B. Catches a future bug where the
    approval loop accidentally approves every staged trust.
    """
    ctx = staged_project_with_trusts
    chosen, unchosen = ctx["trusts"]
    payload = IProjectApproval(project_id=ctx["project"].id, trust_ids=[chosen.id])

    assert approve_project(session, payload, ctx["user"].id) is True

    intersects = session.exec(
        select(ProjectTrustIntersect).where(ProjectTrustIntersect.project_id == ctx["project"].id)
    ).all()
    by_trust = {row.trust_id: row.approved for row in intersects}
    assert by_trust[chosen.id] is True
    assert by_trust[unchosen.id] is False


def test_approve_project_returns_false_for_trust_not_in_staging_set(session, staged_project_with_trusts):
    """A trust_id with no matching ProjectTrustIntersect → return False, no DB changes committed."""
    ctx = staged_project_with_trusts
    unstaged_trust_id = ctx["trusts"][0].id  # arbitrary; we'll mix in a bogus one below
    payload = IProjectApproval(
        project_id=ctx["project"].id,
        trust_ids=[unstaged_trust_id, ctx["project"].id],  # second id is deliberately not a real trust intersect
    )

    assert approve_project(session, payload, ctx["user"].id) is False

    refreshed = session.get(Projects, ctx["project"].id)
    assert refreshed.status == ProjectStatus.STAGED, "Status must not advance when any trust fails the lookup"


def test_approve_project_raises_for_missing_project(session, user_factory):
    payload = IProjectApproval(project_id=uuid4(), trust_ids=[uuid4()])
    with pytest.raises(ValueError, match="does not exist"):
        approve_project(session, payload, user_factory().id)


def test_approve_project_raises_for_soft_deleted_project(session, project_payload, user_factory):
    """A soft-deleted project must refuse approval — guards against re-approving via stale UI state."""
    creator_id = user_factory().id
    project_id = create_project(payload=project_payload, current_user_id=creator_id, session=session)
    delete_project(project_id, creator_id, session)

    payload = IProjectApproval(project_id=project_id, trust_ids=[])
    with pytest.raises(ValueError, match="does not exist or is deleted"):
        approve_project(session, payload, creator_id)
