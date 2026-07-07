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

"""Integration coverage of the estate-wide ``GET /api/models`` endpoint (issue #726).

The cross-project models list joins each model to its owning project (name), the
model's run trusts (``ModelTrustIntersect`` -> ``Trust``) and the owner's display
name, then applies the same access scoping as the projects list: a caller sees
only models whose project they own or have ``ProjectUserAccess`` to, while a
manager (``CAN_MANAGE_PROJECTS``) sees every model. These are SQL-shaped joins +
access filters that pass silently under a mocked session, so they are exercised
against the throwaway Postgres.
"""

from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from flip_api.db.models.main_models import (
    Model,
    ModelTrustIntersect,
    Projects,
    ProjectUserAccess,
    Trust,
)
from flip_api.db.models.user_models import UserProfile
from flip_api.domain.schemas.status import ModelStatus, ProjectStatus, TrustIntersectStatus
from tests.integration.conftest import admin_user, override_verify_token_as

MODELS_URL = "/api/models"


def _add_project(session, project_factory, *, owner_id: UUID, name: str = "Project") -> Projects:
    project = project_factory.build(owner_id=owner_id, name=name, deleted=False, status=ProjectStatus.APPROVED)
    session.add(project)
    session.commit()
    return project


def _add_model(
    session,
    model_factory,
    *,
    project_id: UUID,
    owner_id: UUID,
    name: str = "Model",
    status: ModelStatus = ModelStatus.PREPARED,
) -> Model:
    model = model_factory.build(
        project_id=project_id, owner_id=owner_id, name=name, deleted=False, status=status
    )
    session.add(model)
    session.commit()
    return model


def _grant_access(session, *, project_id: UUID, user_id: UUID) -> None:
    session.add(ProjectUserAccess(project_id=project_id, user_id=user_id))
    session.commit()


def _add_run_trust(session, trust_factory, *, model_id: UUID, name: str, code: str) -> Trust:
    trust = trust_factory.build(name=name, code=code)
    session.add(trust)
    session.commit()
    session.add(
        ModelTrustIntersect(model_id=model_id, trust_id=trust.id, status=TrustIntersectStatus.INITIALISED)
    )
    session.commit()
    return trust


def _ids(payload) -> set[str]:
    return {row["id"] for row in payload["data"]}


def test_lists_only_models_from_projects_the_user_can_access(
    client: TestClient, session, project_factory, model_factory
):
    """A researcher sees models for projects they own or are granted, never others'."""
    user_id = uuid4()
    other_id = uuid4()

    owned = _add_project(session, project_factory, owner_id=user_id, name="Owned")
    granted = _add_project(session, project_factory, owner_id=other_id, name="Granted")
    foreign = _add_project(session, project_factory, owner_id=other_id, name="Foreign")
    _grant_access(session, project_id=granted.id, user_id=user_id)

    m_owned = _add_model(session, model_factory, project_id=owned.id, owner_id=user_id, name="owned-model")
    m_granted = _add_model(session, model_factory, project_id=granted.id, owner_id=other_id, name="granted-model")
    _add_model(session, model_factory, project_id=foreign.id, owner_id=other_id, name="foreign-model")

    override_verify_token_as(user_id)
    response = client.get(MODELS_URL)

    assert response.status_code == 200
    assert _ids(response.json()) == {str(m_owned.id), str(m_granted.id)}


def test_admin_sees_all_models(client: TestClient, session, project_factory, model_factory):
    """A manager (CAN_MANAGE_PROJECTS) sees models across every project."""
    admin_id = admin_user(session)
    other_id = uuid4()

    p1 = _add_project(session, project_factory, owner_id=other_id, name="P1")
    p2 = _add_project(session, project_factory, owner_id=other_id, name="P2")
    m1 = _add_model(session, model_factory, project_id=p1.id, owner_id=other_id, name="m1")
    m2 = _add_model(session, model_factory, project_id=p2.id, owner_id=other_id, name="m2")

    override_verify_token_as(admin_id)
    response = client.get(MODELS_URL)

    assert response.status_code == 200
    assert {str(m1.id), str(m2.id)} <= _ids(response.json())


def test_row_carries_project_name_owner_name_and_run_trusts(
    client: TestClient, session, project_factory, model_factory, trust_factory
):
    """Each row joins project name, owner display name and the model's run trusts."""
    user_id = uuid4()
    session.add(UserProfile(user_id=user_id, name="Dr Ada"))
    session.commit()

    project = _add_project(session, project_factory, owner_id=user_id, name="Stroke triage")
    model = _add_model(session, model_factory, project_id=project.id, owner_id=user_id, name="stroke-v1")
    _add_run_trust(session, trust_factory, model_id=model.id, name="Guy's & St Thomas'", code="GSTT")
    _add_run_trust(session, trust_factory, model_id=model.id, name="King's College Hospital", code="KCH")

    override_verify_token_as(user_id)
    row = next(r for r in client.get(MODELS_URL).json()["data"] if r["id"] == str(model.id))

    assert row["projectId"] == str(project.id)
    assert row["projectName"] == "Stroke triage"
    assert row["ownerName"] == "Dr Ada"
    assert {t["code"] for t in row["trusts"]} == {"GSTT", "KCH"}


def test_model_without_run_trusts_returns_empty_trusts(
    client: TestClient, session, project_factory, model_factory
):
    """A model that has not been dispatched (no ModelTrustIntersect rows) has an empty trust list."""
    user_id = uuid4()
    project = _add_project(session, project_factory, owner_id=user_id, name="Fresh")
    model = _add_model(
        session, model_factory, project_id=project.id, owner_id=user_id, status=ModelStatus.PENDING
    )

    override_verify_token_as(user_id)
    row = next(r for r in client.get(MODELS_URL).json()["data"] if r["id"] == str(model.id))

    assert row["trusts"] == []


def test_status_filter_narrows_results(client: TestClient, session, project_factory, model_factory):
    """``?status=`` returns only models in that lifecycle state."""
    user_id = uuid4()
    project = _add_project(session, project_factory, owner_id=user_id, name="Mixed")
    training = _add_model(
        session, model_factory, project_id=project.id, owner_id=user_id,
        name="live", status=ModelStatus.TRAINING_STARTED,
    )
    _add_model(
        session, model_factory, project_id=project.id, owner_id=user_id,
        name="done", status=ModelStatus.RESULTS_UPLOADED,
    )

    override_verify_token_as(user_id)
    response = client.get(MODELS_URL, params={"status": "TRAINING_STARTED"})

    assert response.status_code == 200
    assert _ids(response.json()) == {str(training.id)}


def test_search_matches_project_name(client: TestClient, session, project_factory, model_factory):
    """Search matches on the owning project's name, not just the model name."""
    user_id = uuid4()
    retino = _add_project(session, project_factory, owner_id=user_id, name="Diabetic retinopathy")
    sepsis = _add_project(session, project_factory, owner_id=user_id, name="Sepsis early warning")
    m_retino = _add_model(session, model_factory, project_id=retino.id, owner_id=user_id, name="grade-v1")
    _add_model(session, model_factory, project_id=sepsis.id, owner_id=user_id, name="tsm-v1")

    override_verify_token_as(user_id)
    response = client.get(MODELS_URL, params={"search": "retinopathy"})

    assert response.status_code == 200
    assert _ids(response.json()) == {str(m_retino.id)}


def test_excludes_soft_deleted_models_and_projects(
    client: TestClient, session, project_factory, model_factory
):
    """Soft-deleted models, and models under a soft-deleted project, are not listed."""
    user_id = uuid4()
    live_project = _add_project(session, project_factory, owner_id=user_id, name="Live")
    kept = _add_model(session, model_factory, project_id=live_project.id, owner_id=user_id, name="kept")

    deleted_model = model_factory.build(
        project_id=live_project.id, owner_id=user_id, name="gone", deleted=True, status=ModelStatus.PREPARED
    )
    session.add(deleted_model)
    deleted_project = project_factory.build(
        owner_id=user_id, name="DeadProject", deleted=True, status=ProjectStatus.APPROVED
    )
    session.add(deleted_project)
    session.commit()
    orphan = _add_model(session, model_factory, project_id=deleted_project.id, owner_id=user_id, name="orphan")

    override_verify_token_as(user_id)
    ids = _ids(client.get(MODELS_URL).json())

    assert str(kept.id) in ids
    assert str(deleted_model.id) not in ids
    assert str(orphan.id) not in ids


def test_results_are_paginated(client: TestClient, session, project_factory, model_factory):
    """The list is paginated: pageSize bounds the page and the wrapper reports the true totals."""
    user_id = uuid4()
    project = _add_project(session, project_factory, owner_id=user_id, name="Many")
    for i in range(3):
        _add_model(session, model_factory, project_id=project.id, owner_id=user_id, name=f"m{i}")

    override_verify_token_as(user_id)
    page_one = client.get(MODELS_URL, params={"pageNumber": 1, "pageSize": 2}).json()

    assert page_one["totalRecords"] == 3
    assert page_one["totalPages"] == 2
    assert len(page_one["data"]) == 2

    page_two = client.get(MODELS_URL, params={"pageNumber": 2, "pageSize": 2}).json()
    assert len(page_two["data"]) == 1
    # No overlap between pages.
    assert _ids(page_one).isdisjoint(_ids(page_two))


def test_invalid_status_param_is_rejected(client: TestClient, session, project_factory, model_factory):
    """An unrecognised status value is a 400, not a silent all-models response."""
    user_id = uuid4()
    override_verify_token_as(user_id)

    response = client.get(MODELS_URL, params={"status": "NOT_A_STATUS"})

    assert response.status_code == 400


def test_multiple_statuses_filter_the_list_at_once(
    client: TestClient, session, project_factory, model_factory
):
    """A comma-separated ``status`` filters to any of the given statuses (drives the group tiles)."""
    user_id = uuid4()
    project = _add_project(session, project_factory, owner_id=user_id, name="Multi")
    pending = _add_model(
        session, model_factory, project_id=project.id, owner_id=user_id, name="p", status=ModelStatus.PENDING
    )
    initiated = _add_model(
        session, model_factory, project_id=project.id, owner_id=user_id, name="i", status=ModelStatus.INITIATED
    )
    _add_model(
        session, model_factory, project_id=project.id, owner_id=user_id, name="prep", status=ModelStatus.PREPARED
    )

    override_verify_token_as(user_id)
    response = client.get(MODELS_URL, params={"status": "PENDING,INITIATED"})

    assert response.status_code == 200
    assert _ids(response.json()) == {str(pending.id), str(initiated.id)}


def test_response_carries_per_status_counts(client: TestClient, session, project_factory, model_factory):
    """The response includes per-status totals over the access-scoped set (the tile numbers)."""
    user_id = uuid4()
    project = _add_project(session, project_factory, owner_id=user_id, name="Counts")
    for _ in range(2):
        _add_model(session, model_factory, project_id=project.id, owner_id=user_id, status=ModelStatus.PENDING)
    _add_model(session, model_factory, project_id=project.id, owner_id=user_id, status=ModelStatus.TRAINING_STARTED)

    override_verify_token_as(user_id)
    counts = client.get(MODELS_URL).json()["statusCounts"]

    assert counts.get("PENDING") == 2
    assert counts.get("TRAINING_STARTED") == 1


def test_status_counts_ignore_the_active_status_filter(
    client: TestClient, session, project_factory, model_factory
):
    """Tile counts show every status even while the list is filtered to one, so tiles stay usable."""
    user_id = uuid4()
    project = _add_project(session, project_factory, owner_id=user_id, name="Facets")
    pending = _add_model(
        session, model_factory, project_id=project.id, owner_id=user_id, status=ModelStatus.PENDING
    )
    _add_model(session, model_factory, project_id=project.id, owner_id=user_id, status=ModelStatus.TRAINING_STARTED)

    override_verify_token_as(user_id)
    body = client.get(MODELS_URL, params={"status": "PENDING"}).json()

    # The list is narrowed to the filtered status...
    assert _ids(body) == {str(pending.id)}
    # ...but the tiles still see the full breakdown.
    assert body["statusCounts"].get("PENDING") == 1
    assert body["statusCounts"].get("TRAINING_STARTED") == 1


def test_status_counts_exclude_inaccessible_projects(
    client: TestClient, session, project_factory, model_factory
):
    """Counts obey the same access scoping as the list — foreign models never inflate a tile."""
    user_id = uuid4()
    other_id = uuid4()
    mine = _add_project(session, project_factory, owner_id=user_id, name="Mine")
    foreign = _add_project(session, project_factory, owner_id=other_id, name="Foreign")
    _add_model(session, model_factory, project_id=mine.id, owner_id=user_id, status=ModelStatus.PENDING)
    _add_model(session, model_factory, project_id=foreign.id, owner_id=other_id, status=ModelStatus.PENDING)

    override_verify_token_as(user_id)
    counts = client.get(MODELS_URL).json()["statusCounts"]

    assert counts.get("PENDING") == 1
