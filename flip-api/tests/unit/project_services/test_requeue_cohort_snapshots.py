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
#

"""Unit tests for POST /projects/{id}/cohort-snapshots — re-queuing the cohort freeze (FLIP#857)."""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust
from flip_api.domain.schemas.status import CohortSnapshotState, DecisionMaker, ProjectStatus
from flip_api.project_services.requeue_cohort_snapshots import router
from flip_api.project_services.services.cohort_snapshot_service import TrustSnapshotState

MODULE = "flip_api.project_services.requeue_cohort_snapshots"
USER_ID = uuid4()
PROJECT_ID = uuid4()
URL = f"/api/projects/{PROJECT_ID}/cohort-snapshots"

FROZEN = Trust(id=uuid4(), name="Frozen Trust")
PENDING = Trust(id=uuid4(), name="Pending Trust")
FAILED = Trust(id=uuid4(), name="Failed Trust")
NEVER = Trust(id=uuid4(), name="Never Asked Trust")
STATES = {
    FROZEN.id: CohortSnapshotState.FROZEN,
    PENDING.id: CohortSnapshotState.PENDING,
    FAILED.id: CohortSnapshotState.FAILED,
    NEVER.id: CohortSnapshotState.FAILED,
}


def _states(project_id, trusts, db):
    return [TrustSnapshotState(trust=trust, state=STATES[trust.id]) for trust in trusts]


@pytest.fixture
def db() -> MagicMock:
    return MagicMock()


@pytest.fixture
def client(db: MagicMock) -> Generator[TestClient, None, None]:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[verify_token] = lambda: USER_ID
    return TestClient(app)


@pytest.fixture
def seams() -> Generator[SimpleNamespace, None, None]:
    with (
        patch(f"{MODULE}.get_project") as get_project,
        patch(f"{MODULE}.get_approved_trusts_for_project") as approved,
        patch(f"{MODULE}.trusts_with_admin", return_value=set()) as with_admin,
        patch(f"{MODULE}.decision_maker_for", return_value=DecisionMaker.HUB) as decision_maker,
        patch(f"{MODULE}.resolve_snapshot_states", side_effect=_states) as resolve,
        patch(f"{MODULE}.queue_cohort_snapshot") as queue,
    ):
        get_project.return_value = SimpleNamespace(status=ProjectStatus.APPROVED)
        approved.return_value = [FROZEN, PENDING, FAILED, NEVER]
        yield SimpleNamespace(
            get_project=get_project,
            approved=approved,
            with_admin=with_admin,
            decision_maker=decision_maker,
            resolve=resolve,
            queue=queue,
        )


def test_requeues_only_missing_or_failed_snapshots(client: TestClient, seams: SimpleNamespace):
    response = client.post(URL)

    assert response.status_code == status.HTTP_200_OK
    body = {entry["trustName"]: entry for entry in response.json()}
    assert {name for name, entry in body.items() if entry["queued"]} == {"Failed Trust", "Never Asked Trust"}
    assert body["Failed Trust"]["status"] == "pending"
    # A frozen trust is skipped unless asked for; a pending one is already queued.
    assert body["Frozen Trust"]["status"] == "frozen"
    assert "include_frozen" in body["Frozen Trust"]["reason"]
    assert body["Pending Trust"]["status"] == "pending"
    assert "already pending" in body["Pending Trust"]["reason"]
    queued_trusts = {call.kwargs["trust"].id for call in seams.queue.call_args_list}
    assert queued_trusts == {FAILED.id, NEVER.id}


def test_include_frozen_rechecks_frozen_trusts(client: TestClient, seams: SimpleNamespace):
    """The recovery for a trust that lost its store: frozen trusts are re-queued too (the trust keeps a membership it
    still holds), while a pending trust is still left alone."""
    response = client.post(URL, params={"include_frozen": "true"})

    assert response.status_code == status.HTTP_200_OK
    body = {entry["trustName"]: entry for entry in response.json()}
    assert body["Frozen Trust"]["queued"] is True
    assert body["Frozen Trust"]["status"] == "pending"
    assert body["Pending Trust"]["queued"] is False
    queued_trusts = {call.kwargs["trust"].id for call in seams.queue.call_args_list}
    assert queued_trusts == {FROZEN.id, FAILED.id, NEVER.id}


def test_covers_only_the_trusts_the_caller_may_decide(client: TestClient, seams: SimpleNamespace):
    """The approval authority, per trust: a site-run trust the caller cannot decide is left alone."""
    seams.with_admin.return_value = {NEVER.id}
    seams.decision_maker.side_effect = lambda user, trust_id, db, has_admin: None if has_admin else DecisionMaker.HUB

    response = client.post(URL)

    assert response.status_code == status.HTTP_200_OK
    assert NEVER.name not in {entry["trustName"] for entry in response.json()}
    assert {call.kwargs["trust"].id for call in seams.queue.call_args_list} == {FAILED.id}
    for call in seams.decision_maker.call_args_list:
        assert call.kwargs["has_admin"] == (call.args[1] == NEVER.id)


def test_forbidden_when_the_caller_may_decide_no_trust(client: TestClient, seams: SimpleNamespace):
    seams.decision_maker.return_value = None

    response = client.post(URL)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    seams.queue.assert_not_called()


@pytest.mark.parametrize("project_status", [ProjectStatus.UNSTAGED, ProjectStatus.STAGED])
def test_conflict_when_the_project_is_not_approved(client: TestClient, seams: SimpleNamespace, project_status):
    seams.get_project.return_value = SimpleNamespace(status=project_status)

    response = client.post(URL)

    assert response.status_code == status.HTTP_409_CONFLICT
    seams.queue.assert_not_called()


def test_not_found_when_the_project_does_not_exist(client: TestClient, seams: SimpleNamespace):
    seams.get_project.side_effect = HTTPException(status_code=404, detail="Project not found")

    response = client.post(URL)

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_a_trust_that_cannot_be_queued_is_reported_not_raised(client: TestClient, seams: SimpleNamespace):
    """One trust's refusal (e.g. no cohort query) is that trust's outcome; the others still queue."""

    def queue(project_id, trust, db):
        if trust.id == FAILED.id:
            raise HTTPException(status_code=409, detail="Project has no cohort query")
        return {"success": "queued"}

    seams.queue.side_effect = queue

    response = client.post(URL)

    assert response.status_code == status.HTTP_200_OK
    body = {entry["trustName"]: entry for entry in response.json()}
    assert body["Failed Trust"]["queued"] is False
    assert body["Failed Trust"]["status"] == "failed"
    assert body["Failed Trust"]["reason"] == "Project has no cohort query"
    assert body["Never Asked Trust"]["queued"] is True
