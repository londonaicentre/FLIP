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

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from trust_api.services.task_handlers import (
    TASK_HANDLERS,
    handle_cohort_query,
    handle_create_imaging,
    handle_delete_imaging,
    handle_get_imaging_status,
    handle_reimport_studies,
    handle_update_user_profile,
)


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
    """Should return failure on HTTP error."""
    mock_make_request.side_effect = Exception("Connection refused")

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


# ---- Delete imaging handler ----


@pytest.mark.asyncio
async def test_handle_delete_imaging_success(mock_make_request):
    """Should call imaging-api to delete project."""
    mock_make_request.return_value = {"status": "deleted"}

    result = await handle_delete_imaging({"imaging_project_id": "img-123"})

    assert result["success"] is True
    call_args = mock_make_request.call_args
    assert call_args.kwargs["method"] == "DELETE"


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

    result = await handle_delete_imaging({"imaging_project_id": "img-123"})

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
