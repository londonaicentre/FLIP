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

import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust
from flip_api.domain.interfaces.trust import IBasicTrust
from flip_api.main import app
from flip_api.trusts_services.get_trusts import get_trusts

# Create a test client instance
client = TestClient(app)

# Mock data to be returned from the database
# Note it can be instantiated with partial fields and it won't complain
# When a class is declared with table=True, SQLModel treats it as a database model, not a strict validation schema.
# This bypasses Pydantic-style validation for missing required fields!!
mock_trusts_data = [
    Trust(id=uuid.uuid4(), name="Trust A"),
    Trust(id=uuid.uuid4(), name="Trust B"),
]


def test_get_trusts_success():
    # Mock the db session and exec function
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = mock_trusts_data

    # Make the test request to the endpoint
    results = get_trusts(db=mock_db, user_id=uuid.uuid4())

    # Assert that the response body matches the mock data
    assert results == [IBasicTrust(id=str(trust.id), name=trust.name) for trust in mock_trusts_data]


def test_get_trusts_endpoint_success():
    # Mock the db session and exec function
    mock_db = MagicMock()
    mock_db.exec.return_value.all.return_value = mock_trusts_data

    # Mock the get_session dependency to return the mocked db session
    app.dependency_overrides[get_session] = lambda: mock_db
    app.dependency_overrides[verify_token] = lambda: uuid.uuid4()

    # Make the test request to the endpoint
    response = client.get("/api/trust")

    # Assert that the response status code is 200
    assert response.status_code == 200
    # Assert that the response body matches the mock data
    assert response.json() == [{"id": str(trust.id), "name": trust.name} for trust in mock_trusts_data]

    # Clean up the dependency override
    del app.dependency_overrides[get_session]
    del app.dependency_overrides[verify_token]


def test_get_trusts_error():
    # Mock the db session and make the exec call raise an exception
    mock_db = MagicMock()
    mock_db.exec.side_effect = Exception("Database error")

    # Mock the get_session dependency to return the mocked db session
    app.dependency_overrides[get_session] = lambda: mock_db
    app.dependency_overrides[verify_token] = lambda: uuid.uuid4()

    # Make the test request to the endpoint
    response = client.get("/api/trust")

    # Assert that the response status code is 500 (Internal Server Error)
    assert response.status_code == 500
    # Assert the error message in the response
    assert response.json() == {"detail": "Internal server error: Database error"}

    # Clean up the dependency override
    del app.dependency_overrides[get_session]
    del app.dependency_overrides[verify_token]
