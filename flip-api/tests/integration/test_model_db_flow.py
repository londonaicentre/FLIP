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

"""Integration coverage of the model_services DB path: save → edit → delete + trust validation.

Hits ``model_services.save_model`` / ``model_services.services.model_service`` against the
throwaway Postgres rather than mocking the session. The fan-out to ``ModelTrustIntersect``
rows on save and the soft-delete + audit write are SQL-shaped and silently passable under
mocked sessions; these tests catch column-rename / FK / default drift.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import select

from flip_api.db.models.main_models import (
    Model,
    ModelsAudit,
    ModelTrustIntersect,
)
from flip_api.domain.interfaces.model import IModelDetails, ISaveModel
from flip_api.domain.schemas.actions import ModelAuditAction
from flip_api.domain.schemas.status import (
    ModelStatus,
    ProjectStatus,
    TrustIntersectStatus,
)
from flip_api.model_services.save_model import save_model
from flip_api.model_services.services.model_service import (
    delete_model,
    edit_model,
    get_model_status,
    validate_trusts,
)


@pytest.fixture
def approved_project_with_trusts(
    session,
    user_factory,
    project_factory,
    trust_factory,
    project_trust_intersect_factory,
):
    """Approved project owned by ``user`` with two approved trusts and one un-approved trust.

    Returns a dict so tests can pull just the bit they care about — the un-approved trust is
    only relevant for the fan-out test, the rest only need ``user`` / ``project``.
    """
    user = user_factory()
    project = project_factory.build(
        owner_id=user.id,
        status=ProjectStatus.APPROVED,
        deleted=False,
    )
    approved_trusts = [trust_factory.build(), trust_factory.build()]
    unapproved_trust = trust_factory.build()

    session.add(project)
    for t in [*approved_trusts, unapproved_trust]:
        session.add(t)
    session.flush()

    for t in approved_trusts:
        session.add(
            project_trust_intersect_factory.build(project_id=project.id, trust_id=t.id, approved=True)
        )
    session.add(
        project_trust_intersect_factory.build(
            project_id=project.id, trust_id=unapproved_trust.id, approved=False
        )
    )
    session.commit()

    return {
        "user": user,
        "project": project,
        "approved_trusts": approved_trusts,
        "unapproved_trust": unapproved_trust,
    }


@pytest.fixture
def model_in_db(session, approved_project_with_trusts, model_factory):
    """A persisted Model owned by the same user as the project, in PENDING status, not deleted.

    PENDING is the only status that ``edit_model_endpoint`` accepts (see ``ModelStatusEdit``),
    so it's the right default for tests that exercise the edit path too.
    """
    ctx = approved_project_with_trusts
    model = model_factory.build(
        project_id=ctx["project"].id,
        owner_id=ctx["user"].id,
        status=ModelStatus.PENDING,
        deleted=False,
    )
    session.add(model)
    session.commit()
    return {"model": model, **ctx}


def test_save_model_persists_model_and_fans_out_to_approved_trusts_only(session, approved_project_with_trusts):
    """A successful save creates the Model and one ModelTrustIntersect per *approved* trust."""
    ctx = approved_project_with_trusts
    payload = ISaveModel(name="my-model", description="desc", projectId=ctx["project"].id)

    created = save_model(
        request=MagicMock(),
        payload=payload,
        db=session,
        user_id=ctx["user"].id,
    )

    persisted = session.get(Model, created.id)
    assert persisted is not None
    assert persisted.name == "my-model"
    assert persisted.description == "desc"
    assert persisted.status == ModelStatus.PENDING
    assert persisted.owner_id == ctx["user"].id
    assert persisted.project_id == ctx["project"].id
    assert persisted.deleted is False

    intersects = session.exec(
        select(ModelTrustIntersect).where(ModelTrustIntersect.model_id == created.id)
    ).all()
    # The un-approved ProjectTrustIntersect must NOT show up in the fan-out — that's the
    # invariant a mocked Session would happily violate.
    assert {row.trust_id for row in intersects} == {t.id for t in ctx["approved_trusts"]}
    assert all(row.status == TrustIntersectStatus.PENDING for row in intersects)


def test_save_model_403_when_user_is_not_project_owner(session, approved_project_with_trusts, user_factory):
    """A user who is not the owner (and lacks CAN_MANAGE_PROJECTS) gets 403."""
    other_user = user_factory()
    payload = ISaveModel(
        name="x", description="d", projectId=approved_project_with_trusts["project"].id
    )

    with pytest.raises(HTTPException) as exc:
        save_model(request=MagicMock(), payload=payload, db=session, user_id=other_user.id)

    assert exc.value.status_code == 403


def test_save_model_400_when_project_not_approved(
    session, user_factory, project_factory, trust_factory, project_trust_intersect_factory
):
    """A project in UNSTAGED (or any non-APPROVED) status must be rejected."""
    user = user_factory()
    project = project_factory.build(owner_id=user.id, status=ProjectStatus.UNSTAGED, deleted=False)
    trust = trust_factory.build()
    session.add_all([project, trust])
    session.flush()
    session.add(project_trust_intersect_factory.build(project_id=project.id, trust_id=trust.id, approved=True))
    session.commit()

    payload = ISaveModel(name="x", description="d", projectId=project.id)
    with pytest.raises(HTTPException) as exc:
        save_model(request=MagicMock(), payload=payload, db=session, user_id=user.id)

    assert exc.value.status_code == 400
    assert "not approved" in exc.value.detail


def test_save_model_400_when_no_approved_trusts(
    session, user_factory, project_factory, trust_factory, project_trust_intersect_factory
):
    """A project with only un-approved trust intersects has nothing to fan out to."""
    user = user_factory()
    project = project_factory.build(owner_id=user.id, status=ProjectStatus.APPROVED, deleted=False)
    trust = trust_factory.build()
    session.add_all([project, trust])
    session.flush()
    session.add(
        project_trust_intersect_factory.build(project_id=project.id, trust_id=trust.id, approved=False)
    )
    session.commit()

    payload = ISaveModel(name="x", description="d", projectId=project.id)
    with pytest.raises(HTTPException) as exc:
        save_model(request=MagicMock(), payload=payload, db=session, user_id=user.id)

    assert exc.value.status_code == 400
    assert "No approved trusts" in exc.value.detail


def test_delete_model_soft_deletes_and_writes_audit(session, model_in_db):
    """``delete_model`` flips ``deleted`` in place and writes a ModelsAudit DELETE row."""
    model = model_in_db["model"]
    user_id = model_in_db["user"].id

    delete_model(model.id, user_id, session)

    refreshed = session.get(Model, model.id)
    assert refreshed is not None, "Soft delete must keep the row for audit"
    assert refreshed.deleted is True

    audits = session.exec(select(ModelsAudit).where(ModelsAudit.model_id == model.id)).all()
    assert len(audits) == 1
    assert audits[0].action == ModelAuditAction.DELETE
    assert audits[0].user_id == user_id


def test_delete_model_raises_when_model_missing(session, user_factory):
    """Non-existent model must raise ValueError, not silently no-op."""
    with pytest.raises(ValueError, match="not found"):
        delete_model(uuid4(), user_factory().id, session)


def test_edit_model_updates_name_and_description_and_writes_audit(session, model_in_db):
    """``edit_model`` overwrites name+description and emits a ModelsAudit EDIT row."""
    model = model_in_db["model"]
    user_id = model_in_db["user"].id

    edit_model(model.id, IModelDetails(name="renamed", description="new desc"), user_id, session)

    refreshed = session.get(Model, model.id)
    assert refreshed.name == "renamed"
    assert refreshed.description == "new desc"

    audits = session.exec(select(ModelsAudit).where(ModelsAudit.model_id == model.id)).all()
    assert len(audits) == 1
    assert audits[0].action == ModelAuditAction.EDIT
    assert audits[0].user_id == user_id


def test_edit_model_raises_when_model_missing(session, user_factory):
    with pytest.raises(ValueError, match="not found"):
        edit_model(uuid4(), IModelDetails(name="x", description=""), user_factory().id, session)


def test_get_model_status_returns_status_for_existing_model(session, model_in_db):
    model = model_in_db["model"]

    result = get_model_status(model.id, session)

    assert result is not None
    assert result.status == ModelStatus.PENDING
    assert result.deleted is False


def test_get_model_status_returns_none_for_missing_model(session):
    assert get_model_status(uuid4(), session) is None


def test_validate_trusts_returns_true_when_all_provided_trusts_are_associated(session, model_in_db, trust_factory):
    """The Trust ↔ ModelTrustIntersect join must hydrate a real list of names."""
    model = model_in_db["model"]
    trust_a = trust_factory.build(name="trust_a")
    trust_b = trust_factory.build(name="trust_b")
    session.add_all([trust_a, trust_b])
    session.flush()
    session.add_all(
        [
            ModelTrustIntersect(model_id=model.id, trust_id=trust_a.id, status=TrustIntersectStatus.PENDING),
            ModelTrustIntersect(model_id=model.id, trust_id=trust_b.id, status=TrustIntersectStatus.PENDING),
        ]
    )
    session.commit()

    assert validate_trusts(model.id, ["trust_a", "trust_b"], session) is True


def test_validate_trusts_returns_false_when_any_provided_trust_is_not_associated(
    session, model_in_db, trust_factory
):
    model = model_in_db["model"]
    trust_a = trust_factory.build(name="trust_a")
    session.add(trust_a)
    session.flush()
    session.add(
        ModelTrustIntersect(model_id=model.id, trust_id=trust_a.id, status=TrustIntersectStatus.PENDING)
    )
    session.commit()

    assert validate_trusts(model.id, ["trust_a", "trust_unknown"], session) is False
