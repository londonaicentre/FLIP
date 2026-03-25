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

import pytest
from fastapi.testclient import TestClient

from trust_api.main import app
from trust_api.routers.cohort import CENTRAL_HUB_API_URL, PRIVATE_API_KEY, PRIVATE_API_KEY_HEADER
from trust_api.routers.schemas import CohortQueryInput


@pytest.fixture
def mock_make_request():
    with patch("trust_api.routers.cohort.make_request", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.asyncio
async def test_post_cohort_query(mock_make_request, client):
    mock_make_request.return_value = {"status": "posted query"}
    query_input = CohortQueryInput(
        encrypted_project_id="test-project-id",
        query_id="test-query-id",
        query_name="Test Query",
        query="SELECT * FROM cohort",
        trust_id="test-trust-id",
    )
    # Using the json= parameter instead of data= to send the payload so that
    # the correct content-type header is set for JSON requests.
    # Needed so that the UUID fields are serialized correctly
    payload = query_input.model_dump()
    response = client.post("/cohort", json=payload)
    assert response.status_code == 200

    # Check calls
    assert mock_make_request.call_count == 2

    # Second call should be to CENTRAL_HUB_API_URL with headers
    call_args = mock_make_request.call_args_list[1]
    assert call_args.kwargs["url"] == f"{CENTRAL_HUB_API_URL}/cohort/results"
    assert call_args.kwargs["headers"] == {PRIVATE_API_KEY_HEADER: PRIVATE_API_KEY}
