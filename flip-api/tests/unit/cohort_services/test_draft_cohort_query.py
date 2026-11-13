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
"""The draft routes with every collaborator mocked: authorisation, the flag and the outcome mapping."""

import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.cohort_services import draft_cohort_query as route
from flip_api.cohort_services.drafting import ledger
from flip_api.cohort_services.drafting.model import FakeDraftModel
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.domain.schemas.status import ProjectStatus

USER_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
RESERVATION = uuid.uuid4()
SECRET = "Women over 60 SECRET-DESCRIPTION-TEXT"
HEADERS = {"Idempotency-Key": "4b1f8a3c-key"}


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "COHORT_DRAFT_ENABLED", True)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.dependency_overrides[get_session] = lambda: MagicMock()
    app.dependency_overrides[verify_token] = lambda: USER_ID
    return TestClient(app)


@pytest.fixture
def project():
    return SimpleNamespace(status=ProjectStatus.UNSTAGED, has_imaging=False)


@pytest.fixture
def mocks(project):
    usage = ledger.Usage(used=3, limit=30, resets_at=datetime(2026, 12, 1, tzinfo=timezone.utc))
    with (
        patch.object(route, "can_modify_project", return_value=True) as can_modify,
        patch.object(route, "get_project_by_id", return_value=project),
        patch.object(route.ledger, "reserve", return_value=RESERVATION) as reserve,
        patch.object(route.ledger, "finish") as finish,
        patch.object(route.ledger, "get_usage", return_value=usage),
        patch.object(route, "get_draft_model", return_value=FakeDraftModel()) as get_model,
    ):
        yield SimpleNamespace(can_modify=can_modify, reserve=reserve, finish=finish, get_model=get_model)


def _post(client, description="Patients with type 2 diabetes", headers=HEADERS, **body):
    return client.post(
        "/api/cohort/draft",
        json={"project_id": str(PROJECT_ID), "description": description, **body},
        headers=headers,
    )


def test_flag_off_hides_both_routes(client, mocks):
    assert _post(client).status_code == 404
    usage = client.get("/api/cohort/draft/usage").json()
    assert usage["enabled"] is False
    assert usage["used"] == 0
    mocks.reserve.assert_not_called()


def test_draft_is_returned_and_charged(client, mocks, enabled):
    response = _post(client)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft"
    assert "person_id" in body["sql"]
    assert body["assumptions"]
    assert body["usage"] == {
        "enabled": True,
        "used": 3,
        "limit": 30,
        "remaining": 27,
        "resets_at": "2026-12-01T00:00:00Z",
    }
    mocks.reserve.assert_called_once()
    assert mocks.reserve.call_args.args[1:] == (USER_ID, PROJECT_ID, HEADERS["Idempotency-Key"])
    mocks.finish.assert_called_once()
    assert mocks.finish.call_args.kwargs == {"consumed": True}


def test_wrong_project_is_forbidden(client, mocks, enabled):
    mocks.can_modify.return_value = False
    assert _post(client).status_code == 403
    mocks.reserve.assert_not_called()


def test_missing_project_is_not_found(client, mocks, enabled):
    with patch.object(route, "get_project_by_id", return_value=None):
        assert _post(client).status_code == 404


@pytest.mark.parametrize("status", [ProjectStatus.STAGED, ProjectStatus.APPROVED])
def test_only_unstaged_projects_draft(client, mocks, enabled, project, status):
    project.status = status
    assert _post(client).status_code == 400
    mocks.reserve.assert_not_called()


def test_project_shape_is_read_server_side(client, mocks, enabled, project):
    project.has_imaging = True
    body = _post(client, description="Chest X-rays", has_imaging=False).json()
    assert "accession_id" in body["sql"]


def test_description_length_and_idempotency_key_are_required(client, mocks, enabled, monkeypatch):
    monkeypatch.setattr(get_settings(), "COHORT_DRAFT_MAX_DESCRIPTION_CHARS", 10)
    assert _post(client).status_code == 400
    assert _post(client, headers={}).status_code == 422
    assert _post(client, headers={"Idempotency-Key": "bad key!"}).status_code == 422


@pytest.mark.parametrize("refusal", list(ledger.Refusal))
def test_refused_reservation_is_an_error_with_usage(client, mocks, enabled, refusal):
    mocks.reserve.return_value = refusal
    body = _post(client).json()
    assert (body["status"], body["error_code"]) == ("error", refusal.value)
    assert body["message"]
    assert body["usage"]["remaining"] == 27
    mocks.get_model.assert_not_called()
    mocks.finish.assert_not_called()


@pytest.mark.parametrize(
    ("description", "status"),
    [("Delete the opted-out rows", "decline"), ("CT scans", "decline")],
)
def test_decline_releases(client, mocks, enabled, description, status):
    body = _post(client, description=description).json()
    assert body["status"] == status
    assert body["message"]
    assert mocks.finish.call_args.kwargs == {"consumed": False}


def test_clarification_releases(client, mocks, enabled, project):
    project.has_imaging = True
    body = _post(client, description="Scans of women over 60").json()
    assert body["status"] == "clarification"
    assert body["question"]
    assert body["options"]
    assert mocks.finish.call_args.kwargs == {"consumed": False}


def test_model_failure_releases_and_hides_detail(client, mocks, enabled):
    with patch.object(route, "run_draft", side_effect=RuntimeError("boom")):
        body = _post(client).json()
    assert (body["status"], body["error_code"]) == ("error", "model_unavailable")
    assert mocks.finish.call_args.kwargs == {"consumed": False}


def test_logs_metadata_never_the_description_or_sql(client, mocks, enabled, caplog):
    with caplog.at_level(logging.INFO, logger="flip_api.cohort_draft"):
        body = _post(client, description=SECRET).json()
    logged = " ".join(r.getMessage() for r in caplog.records if r.name == "flip_api.cohort_draft")
    assert "output_tokens" in logged
    assert str(PROJECT_ID) in logged
    assert "SECRET-DESCRIPTION-TEXT" not in logged
    assert body["sql"] not in logged


def test_usage_when_enabled(client, mocks, enabled):
    assert client.get("/api/cohort/draft/usage").json()["used"] == 3
