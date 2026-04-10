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

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trust_api.routers.imaging import router
from trust_api.routers.schemas import CentralHubProject, CentralHubUser, UpdateProfileRequest

# Create a test FastAPI app and include the router
app = FastAPI()
app.include_router(router)


@pytest.fixture
def mock_make_request():
    with patch("trust_api.routers.imaging.make_request", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.asyncio
async def test_create_imaging_project(mock_make_request, client):
    mock_make_request.return_value = {"status": "success"}
    project = CentralHubProject(
        project_id=uuid4(),
        trust_id=uuid4(),
        project_name="Test Project",
        query="SELECT * FROM studies",
        users=[CentralHubUser(id=uuid4(), email="user@example.com", is_disabled=False)],
    )
    # Using the json= parameter instead of data= to send the payload so that
    # the correct content-type header is set for JSON requests.
    # Needed so that the UUID fields are serialized correctly
    payload = json.loads(project.model_dump_json())
    response = client.post("/imaging/", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "success"}


@pytest.mark.asyncio
async def test_delete_imaging_project(mock_make_request, client):
    mock_make_request.return_value = {"status": "deleted"}
    project_id = "test-project-id"
    response = client.delete(f"/imaging/{project_id}")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}


@pytest.mark.asyncio
async def test_get_imaging_project_status(mock_make_request, client):
    mock_make_request.return_value = {"status": "retrieved"}
    project_id = "test-id"
    query = "encoded-query-string"
    response = client.get(f"/imaging/{project_id}", params={"encoded_query": query})
    assert response.status_code == 200
    assert response.json() == {"status": "retrieved"}


@pytest.mark.asyncio
async def test_reimport_studies(mock_make_request, client):
    mock_make_request.return_value = {"status": "reimported"}
    project_id = "test-id"
    query = "encoded-query-string"
    response = client.put(f"/imaging/reimport/{project_id}", params={"encoded_query": query})
    assert response.status_code == 200
    assert response.json() == {"status": "reimported"}


@pytest.mark.asyncio
async def test_update_profile(mock_make_request, client):
    mock_make_request.return_value = {"status": "updated"}
    request_data = UpdateProfileRequest(email="user@example.com", enabled=True)
    response = client.put("/imaging/users", json=request_data.model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json() == {"status": "updated"}
