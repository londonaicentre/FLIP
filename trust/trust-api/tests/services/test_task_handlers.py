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

import hashlib
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from trust_api.services.task_handlers import (
    AES_KEY_BASE64,
    COHORT_ADMIN_KEY_HEADER,
    TASK_HANDLERS,
    TRUST_INTERNAL_SERVICE_KEY,
    TRUST_INTERNAL_SERVICE_KEY_HEADER,
    handle_cohort_query,
    handle_create_imaging,
    handle_delete_imaging,
    handle_get_imaging_status,
    handle_persist_cohort,
    handle_reimport_studies,
    handle_update_user_profile,
)


def _assert_trust_internal_auth_header(call_args) -> None:
    """Every trust-internal call (imaging-api, data-access-api) must carry the
    trust-internal service key header."""
    headers = call_args.kwargs.get("headers") or {}
    assert headers.get(TRUST_INTERNAL_SERVICE_KEY_HEADER) == TRUST_INTERNAL_SERVICE_KEY


def _assert_cohort_admin_header(call_args) -> None:
    """A cohort-DEFINING write (snapshot create/delete) must additionally carry the
    cohort-admin proof: the SHA-256 of AES_KEY_BASE64, never the key itself (FLIP#857)."""
    headers = call_args.kwargs.get("headers") or {}
    expected = hashlib.sha256(AES_KEY_BASE64.encode()).hexdigest()
    assert headers.get(COHORT_ADMIN_KEY_HEADER) == expected
    # Defence in depth: the raw key must never travel as a header value.
    assert AES_KEY_BASE64 not in headers.values()


@pytest.fixture
def mock_make_request():
    with patch("trust_api.services.task_handlers.make_request", new_callable=AsyncMock) as mock:
        yield mock


# ---- Task handler registry ----


def test_task_handlers_registry():
    """All expected task types should be registered."""
    expected_types = {
        "cohort_query",
        "create_imaging",
        "delete_imaging",
        "get_imaging_status",
        "reimport_studies",
        "update_user_profile",
        "persist_cohort",
    }
    assert set(TASK_HANDLERS.keys()) == expected_types


# ---- Cohort query handler ----


@pytest.mark.asyncio
async def test_handle_cohort_query_success(mock_make_request):
    """Should call data-access-api then push results to hub."""
    mock_make_request.side_effect = [
        {"data": [{"name": "age", "results": [{"value": "30", "count": 5}]}]},  # data-access-api response
        {"message": "ok"},  # hub callback response
    ]

    payload = {
        "query": "SELECT 1",
        "query_name": "Test",
        "encrypted_project_id": "enc123",
        "query_id": "q1",
        "trust_id": "t1",
    }
    result = await handle_cohort_query(payload)

    assert result["success"] is True
    assert mock_make_request.call_count == 2

    # First call should be to data-access-api
    first_call = mock_make_request.call_args_list[0]
    assert first_call.kwargs["method"] == "POST"
    assert "/cohort" in first_call.kwargs["url"]
    # data-access-api now requires the trust-internal service key on /cohort.
    _assert_trust_internal_auth_header(first_call)

    # Second call should be to central hub
    second_call = mock_make_request.call_args_list[1]
    assert second_call.kwargs["method"] == "POST"
    assert "/cohort/results" in second_call.kwargs["url"]


@pytest.mark.asyncio
async def test_handle_cohort_query_invalid_payload():
    """Should return failure on invalid payload."""
    result = await handle_cohort_query({"query": "SELECT 1"})

    assert result["success"] is False
    assert "validation error" in result["error"].lower()


@pytest.mark.asyncio
async def test_handle_cohort_query_error(mock_make_request):
    """When data-access-api fails, the trust still reports an error result to
    the hub so the per-trust UI status leaves "running" and shows "error"."""
    # First call (to data-access-api) fails; second call (to hub) succeeds.
    mock_make_request.side_effect = [
        Exception("Connection refused"),
        {"message": "ok"},
    ]

    payload = {
        "query": "SELECT 1",
        "query_name": "Test",
        "encrypted_project_id": "enc123",
        "query_id": "q1",
        "trust_id": "t1",
    }
    result = await handle_cohort_query(payload)

    assert result["success"] is False
    assert "Connection refused" in result["error"]

    # An error report must have been posted to the hub.
    assert mock_make_request.call_count == 2
    hub_call = mock_make_request.call_args_list[1]
    assert "/cohort/results" in hub_call.kwargs["url"]
    body = hub_call.kwargs["json_body"]
    assert body["query_id"] == "q1"
    assert body["trust_id"] == "t1"
    assert body["record_count"] == 0
    assert body["data"] == []
    assert body["error"] == "Connection refused"


@pytest.mark.asyncio
async def test_handle_cohort_query_error_swallows_hub_post_failure(mock_make_request):
    """If reporting the error to the hub also fails, the handler still returns
    the original error rather than masking it with the hub-post failure."""
    mock_make_request.side_effect = [
        Exception("Database connection failed"),
        Exception("Hub unreachable"),
    ]

    payload = {
        "query": "SELECT 1",
        "query_name": "Test",
        "encrypted_project_id": "enc123",
        "query_id": "q1",
        "trust_id": "t1",
    }
    result = await handle_cohort_query(payload)

    assert result["success"] is False
    assert "Database connection failed" in result["error"]


# ---- Create imaging handler ----


@pytest.mark.asyncio
async def test_handle_create_imaging_success(mock_make_request):
    """Should call imaging-api to create project."""
    mock_make_request.return_value = {"ID": "img-123", "name": "Test Project"}

    payload = {"project_id": str(uuid4()), "trust_id": str(uuid4()), "project_name": "Test"}
    result = await handle_create_imaging(payload)

    assert result["success"] is True
    mock_make_request.assert_called_once()
    call_args = mock_make_request.call_args
    assert call_args.kwargs["method"] == "POST"
    assert "create-project-from-central-hub-project" in call_args.kwargs["url"]
    _assert_trust_internal_auth_header(call_args)


# ---- Delete imaging handler ----


@pytest.mark.asyncio
async def test_handle_delete_imaging_success(mock_make_request):
    """Should call imaging-api to delete project."""
    mock_make_request.return_value = {"status": "deleted"}

    imaging_project_id = str(uuid4())
    result = await handle_delete_imaging({"imaging_project_id": imaging_project_id})

    assert result["success"] is True
    mock_make_request.assert_awaited_once()
    call_args = mock_make_request.call_args
    assert call_args.kwargs["method"] == "DELETE"
    assert call_args.kwargs["url"].endswith(f"/projects/{imaging_project_id}")
    assert "params" not in call_args.kwargs
    _assert_trust_internal_auth_header(call_args)


@pytest.mark.asyncio
async def test_handle_delete_imaging_rejects_non_uuid_id(mock_make_request):
    """Should refuse an id that is not a UUID instead of interpolating it into the URL path."""
    result = await handle_delete_imaging({"imaging_project_id": "../other-resource"})

    assert result["success"] is False
    assert "imaging_project_id" in result["error"]
    mock_make_request.assert_not_called()


# ---- Get imaging status handler ----


@pytest.mark.asyncio
async def test_handle_get_imaging_status_success(mock_make_request):
    """Should call imaging-api status endpoint."""
    mock_make_request.return_value = {"import_status": {"total": 10, "imported": 8}}

    result = await handle_get_imaging_status({
        "imaging_project_id": "img-123",
        "encoded_query": "base64query",
    })

    assert result["success"] is True
    call_args = mock_make_request.call_args
    assert call_args.kwargs["method"] == "GET"
    assert "import_status_count" in call_args.kwargs["url"]
    _assert_trust_internal_auth_header(call_args)


# ---- Reimport studies handler ----


@pytest.mark.asyncio
async def test_handle_reimport_studies_success(mock_make_request):
    """Should call imaging-api reimport endpoint."""
    mock_make_request.return_value = {"status": "reimporting"}

    result = await handle_reimport_studies({
        "imaging_project_id": "img-123",
        "encoded_query": "base64query",
    })

    assert result["success"] is True
    call_args = mock_make_request.call_args
    assert call_args.kwargs["method"] == "PUT"
    assert "reimport" in call_args.kwargs["url"]
    _assert_trust_internal_auth_header(call_args)


# ---- Update user profile handler ----


@pytest.mark.asyncio
async def test_handle_update_user_profile_success(mock_make_request):
    """Should call imaging-api users endpoint."""
    mock_make_request.return_value = {"status": "updated"}

    result = await handle_update_user_profile({
        "email": "user@test.com",
        "enabled": True,
    })

    assert result["success"] is True
    call_args = mock_make_request.call_args
    assert call_args.kwargs["method"] == "PUT"
    assert "/users" in call_args.kwargs["url"]
    _assert_trust_internal_auth_header(call_args)


@pytest.mark.asyncio
async def test_handle_create_imaging_error(mock_make_request):
    """Should return failure on error."""
    mock_make_request.side_effect = Exception("Service unavailable")

    payload = {"project_id": str(uuid4()), "trust_id": str(uuid4()), "project_name": "Test"}
    result = await handle_create_imaging(payload)

    assert result["success"] is False
    assert "Service unavailable" in result["error"]


@pytest.mark.asyncio
async def test_handle_delete_imaging_error(mock_make_request):
    """Should return failure on error."""
    mock_make_request.side_effect = Exception("Service unavailable")

    result = await handle_delete_imaging({"imaging_project_id": str(uuid4())})

    assert result["success"] is False
    assert "Service unavailable" in result["error"]


@pytest.mark.asyncio
async def test_handle_get_imaging_status_error(mock_make_request):
    """Should return failure on error."""
    mock_make_request.side_effect = Exception("Service unavailable")

    result = await handle_get_imaging_status({
        "imaging_project_id": "img-123",
        "encoded_query": "base64query",
    })

    assert result["success"] is False
    assert "Service unavailable" in result["error"]


@pytest.mark.asyncio
async def test_handle_reimport_studies_error(mock_make_request):
    """Should return failure on error."""
    mock_make_request.side_effect = Exception("Service unavailable")

    result = await handle_reimport_studies({
        "imaging_project_id": "img-123",
        "encoded_query": "base64query",
    })

    assert result["success"] is False
    assert "Service unavailable" in result["error"]


@pytest.mark.asyncio
async def test_handle_update_user_profile_error(mock_make_request):
    """Should return failure on error."""
    mock_make_request.side_effect = Exception("Service unavailable")

    result = await handle_update_user_profile({"email": "user@test.com", "enabled": True})

    assert result["success"] is False
    assert "Service unavailable" in result["error"]


# ---- Persist cohort handler (FLIP#857) ----


@pytest.mark.asyncio
async def test_handle_persist_cohort_freezes_via_data_access_and_returns_facts(mock_make_request):
    """Forwards the snapshot request to data-access-api and returns its response as the result."""
    snapshot_facts = {
        "row_count": 24,
        "columns": ["modality", "accession_id"],
        "has_accessions": True,
        "snapshot_at": "2026-08-26T00:00:00+00:00",
        "query_hash": "abc123",
    }
    mock_make_request.return_value = snapshot_facts

    payload = {
        "project_id": str(uuid4()),
        "trust_id": str(uuid4()),
        "encrypted_project_id": "enc123",
        "query": "SELECT * FROM omop.image_occurrence",
        "query_id": str(uuid4()),
    }
    result = await handle_persist_cohort(payload)

    assert result["success"] is True
    assert json.loads(result["result"]) == snapshot_facts

    call = mock_make_request.call_args_list[0]
    assert call.kwargs["method"] == "POST"
    assert call.kwargs["url"].endswith("/cohort/snapshot")
    # Only the encrypted id + query go to data-access-api (its DataframeQuery schema).
    assert call.kwargs["json_body"] == {"encrypted_project_id": "enc123", "query": payload["query"]}
    _assert_trust_internal_auth_header(call)
    # Snapshot creation is a cohort-DEFINING write: it must also carry the cohort-admin proof
    # (FLIP#857), which fl-client cannot produce.
    _assert_cohort_admin_header(call)


@pytest.mark.asyncio
async def test_handle_persist_cohort_invalid_payload():
    """A malformed task payload fails validation before any request is made."""
    result = await handle_persist_cohort({"query": "SELECT 1"})

    assert result["success"] is False
    assert "validation error" in result["error"].lower()


@pytest.mark.asyncio
async def test_handle_persist_cohort_reports_data_access_failure(mock_make_request):
    """A refused snapshot (e.g. below-threshold 403) marks the task FAILED at the hub."""
    mock_make_request.side_effect = Exception("403: Cohort is too small for row-level data to be released.")

    payload = {
        "project_id": str(uuid4()),
        "trust_id": str(uuid4()),
        "encrypted_project_id": "enc123",
        "query": "SELECT * FROM omop.image_occurrence",
    }
    result = await handle_persist_cohort(payload)

    assert result["success"] is False
    assert "too small" in result["error"]
