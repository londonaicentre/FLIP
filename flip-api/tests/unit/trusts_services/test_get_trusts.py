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

"""Unit tests for the authenticated trust-list endpoint (GET /trust).

This single list-of-trusts endpoint powers both the trust pickers and the
Connection Status page. It is deliberately NOT admin-gated: every authenticated
role (Researcher, Viewer, Admin) may read it. The fields it returns are benign
trust metadata — no secrets. Creating a trust (POST /admin/trusts) remains
admin-only.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust
from flip_api.main import app
from flip_api.trusts_services.get_trusts import _as_utc_iso, get_trusts

client = TestClient(app)


def _make_trust(name: str = "GSTT", code: str = "GST", region: str = "London") -> Trust:
    return Trust(
        id=uuid.uuid4(),
        name=name,
        code=code,
        region=region,
        api_key_hash="hash",
        created_at=datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
    )


class TestAsUtcIso:
    """`_as_utc_iso` tags naive timestamps so the browser doesn't treat them as local."""

    def test_returns_none_for_none(self):
        assert _as_utc_iso(None) is None

    def test_appends_z_marker_and_millisecond_precision(self):
        # The Trust columns are `timestamp without time zone`; persistence
        # strips the tz, but the helper must still output a UTC-tagged ISO.
        dt = datetime(2026, 5, 27, 12, 34, 56, 789000)
        out = _as_utc_iso(dt)
        assert out is not None
        assert out.endswith("Z")
        assert out == "2026-05-27T12:34:56.789Z"


def _db_with_trusts_and_counts(trusts, count_rows):
    """Return a session mock whose two `.exec(...).all()` calls drive the
    endpoint's two queries (trust list, then per-trust project counts).
    """
    db = MagicMock()
    trust_exec = MagicMock()
    trust_exec.all.return_value = trusts
    count_exec = MagicMock()
    count_exec.all.return_value = count_rows
    db.exec.side_effect = [trust_exec, count_exec]
    return db


def test_get_trusts_returns_serialised_trusts_with_project_counts():
    trust_a = _make_trust("Trust A", "TA", "London")
    trust_b = _make_trust("Trust B", "TB", "Manchester")
    db = _db_with_trusts_and_counts(
        trusts=[trust_a, trust_b],
        count_rows=[(trust_a.id, 3)],  # Trust B has no rows → count defaults to 0.
    )

    result = get_trusts(db=db, user_id=uuid.uuid4())

    assert len(result) == 2
    by_id = {r.id: r for r in result}
    assert by_id[trust_a.id].project_count == 3
    assert by_id[trust_a.id].name == "Trust A"
    assert by_id[trust_a.id].code == "TA"
    assert by_id[trust_a.id].region == "London"
    assert by_id[trust_a.id].last_heartbeat is None
    # Trust B was absent from the count rows → defaults to 0.
    assert by_id[trust_b.id].project_count == 0


def test_get_trusts_tags_heartbeat_as_utc():
    trust = _make_trust("Beat", "BT", "London")
    trust.last_heartbeat = datetime(2026, 5, 27, 12, 0, 0)
    db = _db_with_trusts_and_counts(trusts=[trust], count_rows=[])

    result = get_trusts(db=db, user_id=uuid.uuid4())

    assert result[0].last_heartbeat is not None
    assert result[0].last_heartbeat.endswith("Z")


def test_get_trusts_serialises_services_health():
    """A reported snapshot is passed through verbatim, with a Z-tagged timestamp."""
    trust = _make_trust("Healthy", "HL", "London")
    services = {
        "trust-api": {"status": "healthy", "version": "0.3.0", "response_ms": None},
        "xnat": {"status": "down", "version": None, "response_ms": None},
    }
    trust.services_health = services
    trust.services_health_at = datetime(2026, 5, 27, 12, 0, 0)
    db = _db_with_trusts_and_counts(trusts=[trust], count_rows=[])

    result = get_trusts(db=db, user_id=uuid.uuid4())

    assert result[0].services == services
    assert result[0].services_updated_at == "2026-05-27T12:00:00.000Z"


def test_get_trusts_nulls_services_when_never_reported():
    """A trust that has never sent a snapshot (pre-collector trust-api) serialises
    both new fields as null — the UI renders every non-core service as No data."""
    trust = _make_trust("Legacy", "LG", "London")
    db = _db_with_trusts_and_counts(trusts=[trust], count_rows=[])

    result = get_trusts(db=db, user_id=uuid.uuid4())

    assert result[0].services is None
    assert result[0].services_updated_at is None


def test_get_trusts_drops_null_trust_ids_from_count_aggregation():
    """The count query is a left-join-style group_by; a NULL `trust_id` row
    is a join orphan and must not poison the counts map.
    """
    trust = _make_trust("Solo", "SOLO", "South")
    db = _db_with_trusts_and_counts(
        trusts=[trust],
        count_rows=[(None, 99), (trust.id, 5)],
    )

    result = get_trusts(db=db, user_id=uuid.uuid4())

    assert len(result) == 1
    assert result[0].project_count == 5


def test_get_trusts_returns_empty_list_when_no_trusts():
    db = _db_with_trusts_and_counts(trusts=[], count_rows=[])

    result = get_trusts(db=db, user_id=uuid.uuid4())

    assert result == []


def test_get_trusts_500_on_database_error():
    db = MagicMock()
    db.exec.side_effect = Exception("db down")

    with pytest.raises(HTTPException) as exc_info:
        get_trusts(db=db, user_id=uuid.uuid4())

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Internal server error" in exc_info.value.detail


def test_get_trusts_re_raises_inner_http_exception():
    """An HTTPException raised mid-query must surface unchanged — the generic
    500 catch-all is for unexpected errors only, not for HTTP-shaped ones.
    """
    db = MagicMock()
    db.exec.side_effect = HTTPException(status_code=418, detail="teapot")

    with pytest.raises(HTTPException) as exc_info:
        get_trusts(db=db, user_id=uuid.uuid4())

    assert exc_info.value.status_code == 418
    assert exc_info.value.detail == "teapot"


def test_endpoint_is_accessible_to_authenticated_non_admin():
    """Regression for #557: a non-admin (no admin permission anywhere in the
    call) must get a 200 with the trust list — never a 403. The endpoint has no
    admin gate, so verifying the token is the only requirement.
    """
    trust = _make_trust("Researcher-visible", "RV", "London")
    db = _db_with_trusts_and_counts(trusts=[trust], count_rows=[(trust.id, 2)])

    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[verify_token] = lambda: uuid.uuid4()
    try:
        response = client.get("/api/trust")
    finally:
        del app.dependency_overrides[get_session]
        del app.dependency_overrides[verify_token]

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Researcher-visible"
    assert body[0]["code"] == "RV"
    assert body[0]["region"] == "London"
    assert body[0]["project_count"] == 2


def test_endpoint_returns_generic_500_detail_on_database_error():
    """The merged endpoint surfaces a generic 500 body — no exception text leak."""
    db = MagicMock()
    db.exec.side_effect = Exception("Database error")

    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[verify_token] = lambda: uuid.uuid4()
    try:
        response = client.get("/api/trust")
    finally:
        del app.dependency_overrides[get_session]
        del app.dependency_overrides[verify_token]

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
