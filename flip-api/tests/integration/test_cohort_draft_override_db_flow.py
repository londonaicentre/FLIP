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
"""The admin override of a user's cohort drafting limits (FLIP#1556) against real Postgres: audited, honoured
by the usage route and the reservation, and ignored once expired."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, select

from flip_api.cohort_services.drafting import ledger
from flip_api.config import get_settings
from flip_api.db.models.main_models import CohortDraftOverride, CohortDraftOverrideAudit, Projects
from flip_api.db.models.user_models import RoleRef, UserRole
from tests.integration.conftest import admin_user, override_verify_token_as

NOW = datetime(2026, 11, 13, 9, 0)


@pytest.fixture
def settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "COHORT_DRAFT_ENABLED", True)
    monkeypatch.setattr(settings, "COHORT_DRAFT_MONTHLY_ALLOWANCE", 1)
    monkeypatch.setattr(settings, "COHORT_DRAFT_MONTHLY_REQUEST_CAP", 90)
    monkeypatch.setattr(settings, "COHORT_DRAFT_RATE_LIMIT_PER_MINUTE", 100)
    return settings


@pytest.fixture
def researcher(session: Session) -> UUID:
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.RESEARCHER.value))
    session.commit()
    return user_id


@pytest.fixture
def project(session: Session, researcher: UUID) -> Projects:
    project = Projects(name="draft", description="d", owner_id=researcher, has_imaging=False)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def _audits(session: Session, target: UUID) -> list[CohortDraftOverrideAudit]:
    session.expire_all()
    query = select(CohortDraftOverrideAudit).where(CohortDraftOverrideAudit.target_user_id == target)
    return sorted(session.exec(query).all(), key=lambda a: a.timestamp)


def _use_one_draft(session: Session, user_id: UUID, project: Projects, key: str, now: datetime = NOW) -> None:
    reservation = ledger.reserve(session, user_id, project.id, key, now)
    assert isinstance(reservation, UUID)
    ledger.finish(session, reservation, consumed=True, model_calls=1, now=now)


def test_set_and_clear_are_audited(client, session, settings, researcher):
    admin = admin_user(session)
    override_verify_token_as(admin)
    url = f"/api/cohort/draft/overrides/{researcher}"

    response = client.put(url, json={"monthly_allowance": 50, "monthly_request_cap": 150, "reason": "Benchmark"})
    assert response.status_code == 200, response.text
    assert response.json()["active"] is True
    response = client.put(url, json={"monthly_allowance": 60, "reason": "More benchmark"})
    assert response.status_code == 200
    assert client.get(url).json()["monthly_request_cap"] is None
    assert client.request("DELETE", url, json={"reason": "Benchmark done"}).status_code == 204
    assert client.get(url).status_code == 404

    first, second, cleared = _audits(session, researcher)
    assert (first.action, first.actor_user_id, first.reason) == ("set", admin, "Benchmark")
    assert (first.old_monthly_allowance, first.new_monthly_allowance) == (None, 50)
    assert (first.old_monthly_request_cap, first.new_monthly_request_cap) == (None, 150)
    assert (second.old_monthly_allowance, second.new_monthly_allowance) == (50, 60)
    assert (second.old_monthly_request_cap, second.new_monthly_request_cap) == (150, None)
    assert (cleared.action, cleared.reason) == ("clear", "Benchmark done")
    assert (cleared.old_monthly_allowance, cleared.new_monthly_allowance) == (60, None)
    assert all(a.timestamp is not None for a in (first, second, cleared))


def test_a_researcher_cannot_set_an_override(client, session, settings, researcher):
    override_verify_token_as(researcher)
    response = client.put(f"/api/cohort/draft/overrides/{researcher}", json={"monthly_allowance": 99, "reason": "me"})
    assert response.status_code == 403
    assert session.get(CohortDraftOverride, researcher) is None
    assert _audits(session, researcher) == []


def test_an_unknown_user_is_refused(client, session, settings):
    override_verify_token_as(admin_user(session))
    response = client.put(f"/api/cohort/draft/overrides/{uuid4()}", json={"monthly_allowance": 5, "reason": "r"})
    assert response.status_code == 404


def test_the_override_changes_the_allowance(client, session, settings, researcher, project):
    _use_one_draft(session, researcher, project, "k0")
    assert ledger.reserve(session, researcher, project.id, "k1", NOW) is ledger.Refusal.EXHAUSTED

    override_verify_token_as(admin_user(session))
    url = f"/api/cohort/draft/overrides/{researcher}"
    assert client.put(url, json={"monthly_allowance": 3, "reason": "Pilot lead"}).status_code == 200

    override_verify_token_as(researcher)
    usage = client.get("/api/cohort/draft/usage").json()
    assert (usage["limit"], usage["used"], usage["remaining"]) == (3, 1, 2)
    _use_one_draft(session, researcher, project, "k1")
    _use_one_draft(session, researcher, project, "k2")
    assert ledger.reserve(session, researcher, project.id, "k3", NOW) is ledger.Refusal.EXHAUSTED


def test_the_override_can_lower_the_request_cap(session, settings, researcher, project):
    session.add(
        CohortDraftOverride(
            user_id=researcher, monthly_allowance=10, monthly_request_cap=2, updated_by=uuid4(), updated_at=NOW
        )
    )
    session.commit()
    # A request reserves the most calls one can make (3), so a cap of 2 refuses the first.
    assert ledger.reserve(session, researcher, project.id, "k0", NOW) is ledger.Refusal.REQUEST_CAP


def test_an_expired_override_no_longer_applies(client, session, settings, researcher, project):
    override_verify_token_as(admin_user(session))
    expires = datetime.now(timezone.utc) + timedelta(days=1)
    response = client.put(
        f"/api/cohort/draft/overrides/{researcher}",
        json={"monthly_allowance": 5, "expires_at": expires.isoformat(), "reason": "One day"},
    )
    assert response.status_code == 200
    stored = session.get(CohortDraftOverride, researcher)
    assert stored is not None
    assert stored.expires_at is not None
    assert stored.expires_at.tzinfo is None  # naive UTC, like every ledger time

    before, after = stored.expires_at - timedelta(minutes=1), stored.expires_at + timedelta(minutes=1)
    assert ledger.limits(session, researcher, before)[0] == 5
    assert ledger.limits(session, researcher, after)[0] == 1
    assert ledger.get_usage(session, researcher, after).limit == 1
    _use_one_draft(session, researcher, project, "k0", before)
    assert ledger.reserve(session, researcher, project.id, "k1", after) is ledger.Refusal.EXHAUSTED
    assert ledger.reserve(session, researcher, project.id, "k2", before) != ledger.Refusal.EXHAUSTED
