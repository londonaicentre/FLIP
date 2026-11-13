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
"""The cohort drafting allowance (FLIP#1556) against real Postgres: reservation, dedup, the monthly
boundary, abandoned reservations, concurrency across connections, and the route end to end."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, create_engine, select

from flip_api.cohort_services.drafting import ledger
from flip_api.config import get_settings
from flip_api.db.models.main_models import CohortDraftRequest, Projects
from flip_api.domain.schemas.status import CohortDraftStatus, ProjectStatus
from tests.integration.conftest import override_verify_token_as

NOW = datetime(2026, 11, 13, 9, 0)


@pytest.fixture
def settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "COHORT_DRAFT_MONTHLY_ALLOWANCE", 3)
    monkeypatch.setattr(settings, "COHORT_DRAFT_RATE_LIMIT_PER_MINUTE", 100)
    return settings


@pytest.fixture
def owner() -> UUID:
    return uuid4()


@pytest.fixture
def project(session: Session, owner: UUID) -> Projects:
    project = Projects(name="draft", description="d", owner_id=owner, has_imaging=False)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def _statuses(session: Session, user_id: UUID) -> list[CohortDraftStatus]:
    session.expire_all()
    rows = session.exec(select(CohortDraftRequest).where(CohortDraftRequest.user_id == user_id)).all()
    return sorted(r.status for r in rows)


def test_reserve_consume_release(session, settings, owner, project):
    first = ledger.reserve(session, owner, project.id, "k1", NOW)
    second = ledger.reserve(session, owner, project.id, "k2", NOW)
    assert isinstance(first, UUID)
    assert isinstance(second, UUID)
    assert ledger.get_usage(session, owner, NOW).used == 2  # in-flight reservations count

    assert ledger.finish(session, first, consumed=True, now=NOW)
    assert ledger.finish(session, second, consumed=False, now=NOW)
    assert not ledger.finish(session, second, consumed=True, now=NOW)  # settled once
    assert ledger.get_usage(session, owner, NOW).used == 1
    assert _statuses(session, owner) == sorted([CohortDraftStatus.CONSUMED, CohortDraftStatus.RELEASED])


def test_duplicate_key_is_never_charged_twice(session, settings, owner, project):
    reservation = ledger.reserve(session, owner, project.id, "same", NOW)
    ledger.finish(session, reservation, consumed=True, now=NOW)
    assert ledger.reserve(session, owner, project.id, "same", NOW) is ledger.Refusal.DUPLICATE
    assert ledger.get_usage(session, owner, NOW).used == 1
    # Keys are per user.
    assert isinstance(ledger.reserve(session, uuid4(), project.id, "same", NOW), UUID)


def test_allowance_is_exhausted_then_resets_at_the_utc_month(session, settings, owner, project):
    for i in range(3):
        ledger.finish(session, ledger.reserve(session, owner, project.id, f"k{i}", NOW), consumed=True, now=NOW)
    assert ledger.reserve(session, owner, project.id, "k3", NOW) is ledger.Refusal.EXHAUSTED

    december = datetime(2026, 12, 1, 0, 0)
    assert ledger.get_usage(session, owner, december).used == 0
    assert isinstance(ledger.reserve(session, owner, project.id, "k4", december), UUID)


def test_released_drafts_do_not_count(session, settings, owner, project):
    for i in range(5):
        ledger.finish(session, ledger.reserve(session, owner, project.id, f"k{i}", NOW), consumed=False, now=NOW)
    assert ledger.get_usage(session, owner, NOW).used == 0


def test_abandoned_reservation_is_released(session, settings, owner, project):
    abandoned = ledger.reserve(session, owner, project.id, "crashed", NOW)
    later = NOW + timedelta(minutes=settings.COHORT_DRAFT_RESERVATION_TTL_MINUTES + 1)
    assert ledger.get_usage(session, owner, later).used == 0
    assert isinstance(ledger.reserve(session, owner, project.id, "next", later), UUID)
    assert not ledger.finish(session, abandoned, consumed=True, now=later)  # already released: uncharged
    assert CohortDraftStatus.RELEASED in _statuses(session, owner)


def test_rate_limit_counts_every_request(session, settings, monkeypatch, owner, project):
    monkeypatch.setattr(settings, "COHORT_DRAFT_RATE_LIMIT_PER_MINUTE", 2)
    for i in range(2):
        ledger.finish(session, ledger.reserve(session, owner, project.id, f"k{i}", NOW), consumed=False, now=NOW)
    assert ledger.reserve(session, owner, project.id, "k2", NOW) is ledger.Refusal.RATE_LIMITED
    assert isinstance(ledger.reserve(session, owner, project.id, "k3", NOW + timedelta(minutes=1)), UUID)


def test_concurrent_reservations_never_exceed_the_allowance(pg_container, settings, owner, project):
    """Eight requests on eight connections race for three drafts; the row lock lets exactly three through."""
    engine = create_engine(pg_container.get_connection_url(), pool_size=8)
    barrier = Barrier(8)

    def attempt(i: int) -> UUID | ledger.Refusal:
        with Session(engine) as s:
            barrier.wait()
            return ledger.reserve(s, owner, project.id, f"race-{i}", NOW)

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
    finally:
        engine.dispose()
    assert sum(isinstance(r, UUID) for r in results) == 3
    assert results.count(ledger.Refusal.EXHAUSTED) == 5


def test_route_end_to_end_with_the_fake_drafter(client, session, settings, monkeypatch, owner, project):
    monkeypatch.setattr(settings, "COHORT_DRAFT_ENABLED", True)
    override_verify_token_as(owner)

    def post(key: str, description: str = "Patients with type 2 diabetes") -> dict:
        response = client.post(
            "/api/cohort/draft",
            json={"project_id": str(project.id), "description": description},
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 200
        return response.json()

    assert client.get("/api/cohort/draft/usage").json()["used"] == 0
    draft = post("a")
    assert draft["status"] == "draft"
    assert draft["usage"]["used"] == 1
    assert post("b", "All CT scans")["status"] == "decline"  # imaging on a tabular project: uncharged
    assert post("a")["error_code"] == "duplicate_request"
    assert client.get("/api/cohort/draft/usage").json()["used"] == 1

    project.status = ProjectStatus.STAGED
    session.add(project)
    session.commit()
    response = client.post(
        "/api/cohort/draft",
        json={"project_id": str(project.id), "description": "x"},
        headers={"Idempotency-Key": "c"},
    )
    assert response.status_code == 400
