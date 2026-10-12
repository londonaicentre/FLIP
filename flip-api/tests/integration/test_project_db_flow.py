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

"""Integration coverage of the project_services DB path: create → fetch → soft-delete → trust decisions.

Hits ``project_services.services.project_services`` against the throwaway
Postgres rather than mocking the session. Unit tests for the same code mock
``Session`` and never catch SQL-shaped bugs (wrong column names, missing
relationships, default mismatches, etc.); these do.
"""

import re
import threading
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, col, create_engine, select

from flip_api.db.models.main_models import (
    Projects,
    ProjectsAudit,
    ProjectTrustIntersect,
    ProjectUserAccess,
    Queries,
    XNATProjectStatus,
)
from flip_api.db.models.user_models import UserProfile
from flip_api.domain.interfaces.project import IProjectApproval
from flip_api.domain.schemas.actions import ProjectAuditAction
from flip_api.domain.schemas.projects import ProjectDetails
from flip_api.domain.schemas.status import ProjectStatus, TrustApprovalStatus, XNATImageStatus
from flip_api.project_services.get_projects import get_projects_paginated_orm
from flip_api.project_services.services.project_services import (
    InvalidTrustDecisionsError,
    TrustDecisionOutcome,
    create_project,
    delete_project,
    get_approved_trusts_for_project,
    get_project,
    get_reimport_queries_service,
    get_trusts_approval_status_for_project,
    record_trust_decisions,
    stage_project_service,
    unstage_project_service,
)
from flip_api.utils.paging_utils import get_filter_details, get_paging_details


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

    access_rows = session.exec(select(ProjectUserAccess).where(ProjectUserAccess.project_id == new_project_id)).all()
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


def test_create_project_persists_has_imaging_false_and_returns_it(session, project_payload, user_factory):
    """A tabular-only project keeps has_imaging=False through create and get_project (FLIP#1071)."""
    creator_id = user_factory().id
    payload = project_payload.model_copy(update={"has_imaging": False})

    new_project_id = create_project(payload=payload, current_user_id=creator_id, session=session)

    assert session.get(Projects, new_project_id).has_imaging is False
    assert get_project(new_project_id, session).has_imaging is False
    # The default stays imaging-on for every existing caller.
    default_id = create_project(payload=project_payload, current_user_id=creator_id, session=session)
    assert session.get(Projects, default_id).has_imaging is True


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
    """A STAGED project plus two PENDING ProjectTrustIntersect rows.

    Mirrors the real precondition for ``record_trust_decisions``: ``stage_project_service`` builds one
    PENDING intersect per selected trust and the project sits in STAGED until every trust is decided.
    """
    user = user_factory()
    project = project_factory.build(owner_id=user.id, status=ProjectStatus.STAGED, deleted=False)
    trusts = [trust_factory.build(), trust_factory.build()]

    session.add(UserProfile(user_id=user.id, name="Ada Approver"))
    session.add(project)
    for t in trusts:
        session.add(t)
    session.flush()
    for t in trusts:
        session.add(
            project_trust_intersect_factory.build(
                project_id=project.id, trust_id=t.id, status=TrustApprovalStatus.PENDING
            )
        )
    session.commit()

    return {"user": user, "project": project, "trusts": trusts}


def _decide(session, ctx, approve=(), decline=(), user_id: UUID | None = None) -> TrustDecisionOutcome:
    payload = IProjectApproval(
        project_id=ctx["project"].id,
        trust_ids=[t.id for t in approve],
        declined_trust_ids=[t.id for t in decline],
    )
    return record_trust_decisions(session, payload, user_id or ctx["user"].id)


def _intersects(session, project_id: UUID) -> dict[UUID, ProjectTrustIntersect]:
    rows = session.exec(select(ProjectTrustIntersect).where(ProjectTrustIntersect.project_id == project_id)).all()
    return {row.trust_id: row for row in rows}


def _audits(session, project_id: UUID) -> list[tuple[ProjectAuditAction, UUID | None]]:
    rows = session.exec(
        select(ProjectsAudit).where(ProjectsAudit.project_id == project_id).order_by(col(ProjectsAudit.audit_date))
    ).all()
    return [(row.action, row.trust_id) for row in rows]


def test_approving_every_trust_approves_the_project_and_attributes_each_decision(session, staged_project_with_trusts):
    """Every trust approved → project APPROVED, each decision carries its decider, one audit row per trust."""
    ctx = staged_project_with_trusts
    a, b = ctx["trusts"]

    assert _decide(session, ctx, approve=[a, b]).project_status == ProjectStatus.APPROVED

    assert session.get(Projects, ctx["project"].id).status == ProjectStatus.APPROVED
    for row in _intersects(session, ctx["project"].id).values():
        assert row.status == TrustApprovalStatus.APPROVED
        assert row.decided_by == ctx["user"].id
        assert row.decided_at is not None
    assert sorted(_audits(session, ctx["project"].id), key=str) == sorted(
        [
            (ProjectAuditAction.APPROVE_TRUST, a.id),
            (ProjectAuditAction.APPROVE_TRUST, b.id),
            (ProjectAuditAction.APPROVE, None),
        ],
        key=str,
    )


def test_declining_a_trust_records_the_refusal_against_that_trust(session, staged_project_with_trusts):
    """Approve one, decline the other → project APPROVED, and the refusal is a recorded decision, not a gap."""
    ctx = staged_project_with_trusts
    chosen, refused = ctx["trusts"]

    assert _decide(session, ctx, approve=[chosen], decline=[refused]).project_status == ProjectStatus.APPROVED

    rows = _intersects(session, ctx["project"].id)
    assert rows[chosen.id].status == TrustApprovalStatus.APPROVED
    assert rows[refused.id].status == TrustApprovalStatus.DECLINED
    assert rows[refused.id].decided_by == ctx["user"].id
    assert rows[refused.id].decided_at is not None
    assert (ProjectAuditAction.DECLINE_TRUST, refused.id) in _audits(session, ctx["project"].id)


def test_one_approval_approves_the_project_and_leaves_the_rest_pending(session, staged_project_with_trusts):
    """Approving some trusts approves the project and leaves the others PENDING, to decide later (FLIP#1258).

    The undecided trust stays visibly undecided — no decider, no date — rather than being silently dropped, the
    failure FLIP#1318 ended.
    """
    ctx = staged_project_with_trusts
    chosen, undecided = ctx["trusts"]

    assert _decide(session, ctx, approve=[chosen]).project_status == ProjectStatus.APPROVED

    assert session.get(Projects, ctx["project"].id).status == ProjectStatus.APPROVED
    rows = _intersects(session, ctx["project"].id)
    assert rows[chosen.id].status == TrustApprovalStatus.APPROVED
    assert rows[undecided.id].status == TrustApprovalStatus.PENDING
    assert rows[undecided.id].decided_by is None
    assert ProjectAuditAction.APPROVE in {action for action, _ in _audits(session, ctx["project"].id)}


def test_declining_every_trust_keeps_the_project_staged(session, staged_project_with_trusts):
    """All trusts declined → the project stays STAGED (to be unstaged and reconsidered), never APPROVED."""
    ctx = staged_project_with_trusts
    a, b = ctx["trusts"]

    assert _decide(session, ctx, decline=[a, b]).project_status == ProjectStatus.STAGED

    assert session.get(Projects, ctx["project"].id).status == ProjectStatus.STAGED
    assert {row.status for row in _intersects(session, ctx["project"].id).values()} == {TrustApprovalStatus.DECLINED}
    assert sorted(_audits(session, ctx["project"].id), key=str) == sorted(
        [(ProjectAuditAction.DECLINE_TRUST, a.id), (ProjectAuditAction.DECLINE_TRUST, b.id)], key=str
    )


def test_changing_a_decision_while_staged_records_a_new_decision(session, staged_project_with_trusts, user_factory):
    """A reversal is a new decision by its own decider; a decision re-sent unchanged is not re-recorded."""
    ctx = staged_project_with_trusts
    reversed_trust, kept = ctx["trusts"]
    first, second = ctx["user"].id, user_factory().id

    first_outcome = _decide(session, ctx, decline=[reversed_trust, kept], user_id=first)
    assert first_outcome.project_status == ProjectStatus.STAGED
    kept_decided_at = _intersects(session, ctx["project"].id)[kept.id].decided_at

    second_outcome = _decide(session, ctx, approve=[reversed_trust], decline=[kept], user_id=second)
    assert second_outcome.project_status == ProjectStatus.APPROVED

    rows = _intersects(session, ctx["project"].id)
    assert rows[reversed_trust.id].status == TrustApprovalStatus.APPROVED
    assert rows[reversed_trust.id].decided_by == second
    assert rows[kept.id].status == TrustApprovalStatus.DECLINED
    assert rows[kept.id].decided_by == first
    assert rows[kept.id].decided_at == kept_decided_at
    audits = _audits(session, ctx["project"].id)
    assert [action for action, trust_id in audits if trust_id == reversed_trust.id] == [
        ProjectAuditAction.DECLINE_TRUST,
        ProjectAuditAction.APPROVE_TRUST,
    ]
    assert [action for action, trust_id in audits if trust_id == kept.id] == [ProjectAuditAction.DECLINE_TRUST]


def test_rejects_a_trust_outside_the_staging_set_and_writes_nothing(session, staged_project_with_trusts):
    """A trust id with no intersect for the project is refused, naming it, and no decision is written for any
    trust."""
    ctx = staged_project_with_trusts
    stranger = uuid4()
    payload = IProjectApproval(
        project_id=ctx["project"].id,
        trust_ids=[ctx["trusts"][0].id, stranger],
    )

    with pytest.raises(InvalidTrustDecisionsError, match=str(stranger)):
        record_trust_decisions(session, payload, ctx["user"].id)
    session.rollback()

    assert session.get(Projects, ctx["project"].id).status == ProjectStatus.STAGED
    assert {row.status for row in _intersects(session, ctx["project"].id).values()} == {TrustApprovalStatus.PENDING}
    assert _audits(session, ctx["project"].id) == []


def test_rejects_a_trust_both_approved_and_declined(session, staged_project_with_trusts):
    ctx = staged_project_with_trusts
    a, _ = ctx["trusts"]

    with pytest.raises(InvalidTrustDecisionsError, match="both approved and declined"):
        _decide(session, ctx, approve=[a], decline=[a])
    session.rollback()
    assert _audits(session, ctx["project"].id) == []


def test_a_decline_then_an_approval_starts_only_the_approved_trust(session, staged_project_with_trusts):
    """A decline alone leaves the project STAGED and starts nothing; the approval that follows approves it and
    starts that trust only — the declined one is never activated."""
    ctx = staged_project_with_trusts
    early, late = ctx["trusts"]

    staged = _decide(session, ctx, decline=[late])
    approved = _decide(session, ctx, approve=[early])

    assert (staged.project_status, staged.activated_trust_ids) == (ProjectStatus.STAGED, [])
    assert (approved.project_status, approved.activated_trust_ids) == (ProjectStatus.APPROVED, [early.id])


def test_refuses_changing_a_decision_once_the_project_is_approved(session, staged_project_with_trusts):
    """Checked under the project lock, so a save that lost a race to another approver's approval is refused
    rather than rewriting a final decision of an approved project."""
    ctx = staged_project_with_trusts
    a, b = ctx["trusts"]
    _decide(session, ctx, approve=[a, b])

    with pytest.raises(InvalidTrustDecisionsError):
        _decide(session, ctx, decline=[a])
    session.rollback()

    assert _intersects(session, ctx["project"].id)[a.id].status == TrustApprovalStatus.APPROVED
    assert ProjectAuditAction.DECLINE_TRUST not in {action for action, _ in _audits(session, ctx["project"].id)}


def test_concurrent_saves_are_serialised_on_the_project_lock(
    pg_container, session, staged_project_with_trusts, user_factory
):
    """Two approvers deciding the last two pending trusts at once must not each see the other's trust as still
    pending — both would leave the project STAGED with every trust decided. The second save waits for the
    project lock, then reads the first's committed decision and approves the project."""
    ctx = staged_project_with_trusts
    project_id = ctx["project"].id
    first, second = ctx["trusts"]
    engine = create_engine(pg_container.get_connection_url())
    outcome: dict[str, TrustDecisionOutcome] = {}

    def second_approver() -> None:
        with Session(engine) as other:
            payload = IProjectApproval(project_id=project_id, trust_ids=[second.id])
            outcome["second"] = record_trust_decisions(other, payload, user_factory().id)

    try:
        with Session(engine) as holder:
            # The first approver's save, mid-flight: project locked, its trust decided but not yet committed.
            holder.exec(select(Projects).where(Projects.id == project_id).with_for_update()).one()
            row = holder.exec(
                select(ProjectTrustIntersect).where(
                    ProjectTrustIntersect.project_id == project_id, ProjectTrustIntersect.trust_id == first.id
                )
            ).one()
            row.status = TrustApprovalStatus.APPROVED
            holder.add(row)
            holder.flush()

            racer = threading.Thread(target=second_approver)
            racer.start()
            racer.join(timeout=1.0)
            assert racer.is_alive(), "the second save must wait for the first to release the project lock"

            holder.commit()
            racer.join(timeout=10.0)
            assert not racer.is_alive()
    finally:
        engine.dispose()

    assert outcome["second"].project_status == ProjectStatus.APPROVED
    assert set(outcome["second"].activated_trust_ids) == {first.id, second.id}


def test_record_trust_decisions_raises_for_missing_project(session, user_factory):
    payload = IProjectApproval(project_id=uuid4(), trust_ids=[uuid4()])
    with pytest.raises(ValueError, match="does not exist"):
        record_trust_decisions(session, payload, user_factory().id)


def test_record_trust_decisions_raises_for_soft_deleted_project(session, project_payload, user_factory):
    """A soft-deleted project must refuse decisions — guards against deciding via stale UI state."""
    creator_id = user_factory().id
    project_id = create_project(payload=project_payload, current_user_id=creator_id, session=session)
    delete_project(project_id, creator_id, session)

    payload = IProjectApproval(project_id=project_id, trust_ids=[])
    with pytest.raises(ValueError, match="does not exist or is deleted"):
        record_trust_decisions(session, payload, creator_id)


def test_unstage_then_restage_resets_every_trust_to_pending_and_keeps_the_decisions_audited(
    session, staged_project_with_trusts
):
    """All declined → unstage → re-stage starts every trust PENDING; the audit is the record that survives."""
    ctx = staged_project_with_trusts
    project_id, user_id = ctx["project"].id, ctx["user"].id
    a, b = ctx["trusts"]
    _decide(session, ctx, decline=[a, b])

    unstage_project_service(project_id, user_id, session)
    stage_project_service(project_id, [a.id, b.id], user_id, session)

    rows = _intersects(session, project_id)
    assert {row.status for row in rows.values()} == {TrustApprovalStatus.PENDING}
    assert {row.decided_by for row in rows.values()} == {None}
    audits = _audits(session, project_id)
    assert (ProjectAuditAction.DECLINE_TRUST, a.id) in audits
    assert (ProjectAuditAction.DECLINE_TRUST, b.id) in audits
    assert [action for action, _ in audits][-2:] == [ProjectAuditAction.UNSTAGE, ProjectAuditAction.STAGE]


def test_trust_approval_status_reports_each_decision_and_its_decider(session, staged_project_with_trusts):
    """The project's trust list carries status, decider id + display name and date, straight from SQL."""
    ctx = staged_project_with_trusts
    chosen, refused = ctx["trusts"]
    _decide(session, ctx, approve=[chosen], decline=[refused])

    by_id = {t.id: t for t in get_trusts_approval_status_for_project(ctx["project"].id, session)}

    assert by_id[chosen.id].status == TrustApprovalStatus.APPROVED
    assert by_id[refused.id].status == TrustApprovalStatus.DECLINED
    for trust in by_id.values():
        assert trust.decided_by == ctx["user"].id
        assert trust.decided_by_name == "Ada Approver"
        assert trust.decided_at is not None


def test_trust_approval_status_lists_undecided_trusts_and_deciders_without_a_profile(
    session, staged_project_with_trusts, user_factory
):
    """The decider join is an outer join: a freshly staged trust (no decider) and a decider with no
    UserProfile row must both still list the trust, or the approval card has nothing to decide."""
    ctx = staged_project_with_trusts
    pending, decided = ctx["trusts"]
    no_profile = user_factory().id
    _decide(session, ctx, decline=[decided], user_id=no_profile)

    by_id = {t.id: t for t in get_trusts_approval_status_for_project(ctx["project"].id, session)}

    assert (by_id[pending.id].status, by_id[pending.id].decided_by, by_id[pending.id].decided_at) == (
        TrustApprovalStatus.PENDING,
        None,
        None,
    )
    assert (by_id[decided.id].decided_by, by_id[decided.id].decided_by_name) == (no_profile, None)
    # Exactly one UTC marker: the browser reads `…+00:00Z` as an invalid date.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", by_id[decided.id].decided_at)


def test_approved_trusts_exclude_declined_and_undecided_trusts(
    session, staged_project_with_trusts, trust_factory, project_trust_intersect_factory
):
    """Only APPROVED trusts are selected downstream (imaging, models, FL jobs)."""
    ctx = staged_project_with_trusts
    chosen, refused = ctx["trusts"]
    undecided = trust_factory.build()
    session.add(undecided)
    session.flush()
    session.add(
        project_trust_intersect_factory.build(
            project_id=ctx["project"].id, trust_id=undecided.id, status=TrustApprovalStatus.PENDING
        )
    )
    session.commit()
    _decide(session, ctx, approve=[chosen], decline=[refused])

    assert [t.id for t in get_approved_trusts_for_project(ctx["project"].id, session)] == [chosen.id]


@pytest.fixture
def project_eligible_for_reimport(session, user_factory, trust_factory, project_payload):
    """A live project wired up exactly as the reimport sweep expects to find it.

    ``get_reimport_queries_service`` joins ``Queries`` → ``XNATProjectStatus`` → ``Trust`` and
    additionally requires ``XNATProjectStatus.query_at_creation == Queries.id``, so all four rows
    have to line up before the sweep will return anything at all.
    """
    creator_id = user_factory().id
    project_id = create_project(payload=project_payload, current_user_id=creator_id, session=session)

    trust = trust_factory.build()
    session.add(trust)

    query = Queries(name="Cohort", query="SELECT 1", project_id=project_id, created_by=creator_id)
    session.add(query)
    session.flush()

    session.add(
        XNATProjectStatus(
            xnat_project_id=uuid4(),
            project_id=project_id,
            trust_id=trust.id,
            retrieve_image_status=XNATImageStatus.CREATED,
            query_at_creation=query.id,
            reimport_count=0,
        )
    )
    session.commit()

    return {"project_id": project_id, "creator_id": creator_id, "trust": trust, "query": query}


def test_reimport_sweep_returns_queries_for_a_live_project(session, project_eligible_for_reimport):
    """Baseline: the sweep must still pick up a live project, or the deleted-project test proves nothing."""
    ctx = project_eligible_for_reimport

    reimport_queries = get_reimport_queries_service(max_reimport_count=5, session=session)

    assert len(reimport_queries) == 1
    assert reimport_queries[0].query_id == ctx["query"].id
    assert reimport_queries[0].trust_id == ctx["trust"].id


def test_reimport_sweep_skips_a_soft_deleted_project(session, project_eligible_for_reimport):
    """A soft-deleted project must drop out of the reimport sweep (FLIP#963).

    Soft delete deliberately leaves Trust imaging intact and no longer writes
    ``retrieve_image_status = DELETED``, so the status flag can no longer be the gate. Without the
    ``Projects.deleted`` predicate the scheduled sweep would keep queueing ``REIMPORT_STUDIES`` for
    a deleted project, pulling *new* patient imaging into a Trust's XNAT after deletion.
    """
    ctx = project_eligible_for_reimport
    delete_project(ctx["project_id"], ctx["creator_id"], session)

    assert get_reimport_queries_service(max_reimport_count=5, session=session) == []

    status_row = session.exec(select(XNATProjectStatus).where(XNATProjectStatus.project_id == ctx["project_id"])).one()
    assert status_row.retrieve_image_status == XNATImageStatus.CREATED, (
        "Imaging status must be untouched by the delete — the project row is what gates the sweep"
    )


def test_list_projects_project_type_filter_selects_by_has_imaging(session, project_payload, user_factory):
    """FLIP#1071: ``projectType`` narrows the list to imaging or tabular-only projects; absent, both are listed."""
    creator_id = user_factory().id
    imaging_id = create_project(payload=project_payload, current_user_id=creator_id, session=session)
    tabular_id = create_project(
        payload=project_payload.model_copy(update={"name": "Tabular", "has_imaging": False}),
        current_user_id=creator_id,
        session=session,
    )

    def listed(params: dict[str, str | UUID]) -> set:
        page = get_projects_paginated_orm(
            session=session,
            user_id=creator_id,
            paging_details=get_paging_details(),
            filter_details=get_filter_details(params),
        )
        return {project.id for project in page.data}

    assert listed({"projectType": "omop_only"}) == {tabular_id}
    assert listed({"projectType": "imaging"}) == {imaging_id}
    assert listed({}) >= {imaging_id, tabular_id}
