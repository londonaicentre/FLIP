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

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust
from flip_api.domain.interfaces.trust import ITrustHealth
from flip_api.main import app
from flip_api.trusts_services.trusts_health_check import check_trust_health

client = TestClient(app)

# ---- Fixtures ----


@pytest.fixture
def mock_trusts_data():
    return [
        Trust(id=uuid4(), name="Trust A", endpoint="http://trust-a.com"),
        Trust(id=uuid4(), name="Trust B", endpoint="http://trust-b.com"),
    ]


@pytest.fixture
def mock_trust():
    return Trust(id=uuid4(), name="Trust A", endpoint="http://trust-a.com")


@pytest.fixture
def mock_headers():
    return {"Authorization": "Bearer test token"}


# ---- Testing the endpoint ----


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get", new_callable=AsyncMock)
async def test_check_trusts_health_success(mock_http_get, mock_trusts_data):
    # Return a 200 response for all health checks
    mock_http_get.return_value.status_code = 200

    # Mock the db session
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = mock_trusts_data

    app.dependency_overrides[get_session] = lambda: mock_db

    response = client.get("/api/trust/health")

    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]["online"] is True
    assert response.json()[1]["online"] is True

    del app.dependency_overrides[get_session]


# Test for error handling when no trusts are found in the database
@pytest.mark.asyncio
async def test_check_trusts_health_no_trusts_found():
    # Mock the db session to return an empty list
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = []

    # Mock the get_session dependency to return the mocked db session
    app.dependency_overrides[get_session] = lambda: mock_db

    # Make the test request to the endpoint
    response = client.get("/api/trust/health")

    # Assert that the response status code is 404
    assert response.status_code == 404
    # Assert the correct error message in the response
    assert response.json() == {"detail": "No trusts found"}

    # Clean up the dependency overrides
    del app.dependency_overrides[get_session]


# Test for error handling when the health endpoint request fails
@pytest.mark.asyncio
async def test_check_trusts_health_http_error(mock_trusts_data):
    # Mock the db session and return some mock trusts
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = mock_trusts_data

    # Mock the get_session dependency to return the mocked db session
    app.dependency_overrides[get_session] = lambda: mock_db

    # Mock httpx.AsyncClient to simulate a failed health check request
    mock_httpx = AsyncMock()
    mock_httpx.get.side_effect = httpx.RequestError("Health check failed", request=None)
    app.dependency_overrides[httpx.AsyncClient] = lambda: mock_httpx

    # Make the test request to the endpoint
    response = client.get("/api/trust/health")

    # Assert that the response status code is 200
    assert response.status_code == 200
    # Assert that the response contains the TrustHealth object with online = False
    assert len(response.json()) == 2
    assert response.json()[0]["online"] is False
    assert response.json()[1]["online"] is False

    # Clean up the dependency overrides
    del app.dependency_overrides[get_session]
    del app.dependency_overrides[httpx.AsyncClient]


# Test for error handling when an exception is raised in the overall process
@pytest.mark.asyncio
async def test_check_trusts_health_internal_server_error():
    # Mock the db session to simulate an exception being thrown
    mock_db = MagicMock()
    mock_db.exec.side_effect = Exception("Database error")

    # Mock the get_session dependency to return the mocked db session
    app.dependency_overrides[get_session] = lambda: mock_db

    # Make the test request to the endpoint
    response = client.get("/api/trust/health")

    # Assert that the response status code is 500
    assert response.status_code == 500
    # Assert the error message in the response
    assert response.json() == {"detail": "Internal server error: Database error"}

    # Clean up the dependency overrides
    del app.dependency_overrides[get_session]


# --- Testing the function check_trust_health ----


@pytest.mark.asyncio
async def test_check_trust_health_success(mock_trust, mock_headers):
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value.status_code = 200

    response = await check_trust_health(client=mock_client, trust=mock_trust, headers=mock_headers)

    assert isinstance(response, ITrustHealth)
    assert response.trust_id == mock_trust.id
    assert response.trust_name == mock_trust.name
    assert response.online is True


@pytest.mark.asyncio
async def test_check_trust_health_unhealthy_status_code(mock_trust, mock_headers):
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value.status_code = 503  # Simulate unhealthy

    response = await check_trust_health(client=mock_client, trust=mock_trust, headers=mock_headers)

    assert isinstance(response, ITrustHealth)
    assert response.trust_id == mock_trust.id
    assert response.trust_name == mock_trust.name
    assert response.online is False


@pytest.mark.asyncio
async def test_check_trust_health_exception(mock_trust, mock_headers):
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = httpx.RequestError("Connection failed", request=None)

    response = await check_trust_health(client=mock_client, trust=mock_trust, headers=mock_headers)

    assert isinstance(response, ITrustHealth)
    assert response.trust_id == mock_trust.id
    assert response.trust_name == mock_trust.name
    assert response.online is False
