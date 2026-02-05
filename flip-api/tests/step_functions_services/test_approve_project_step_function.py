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

import pytest
from fastapi.testclient import TestClient

from flip_api.domain.interfaces.trust import ITrust
from flip_api.main import app
from flip_api.step_functions_services.approve_project_step_function import (
    get_session,
    verify_token,
)

client = TestClient(app)


trust_id_1 = uuid4()
trust_id_2 = uuid4()


@pytest.fixture
def project_id():
    return str(uuid4())


@pytest.fixture
def request_body():
    return {"trusts": [str(trust_id_1), str(trust_id_2)]}


@pytest.fixture
def mock_trusts():
    return [
        ITrust(id=trust_id_1, name="Trust 1", endpoint="https://trust1.endpoint/api"),
        ITrust(
            id=trust_id_2,
            name="Trust 2",
            endpoint="https://trust2.endpoint/api",
            flClientEndpoint="https://trust2.endpoint/fl",
        ),
    ]


@pytest.fixture(autouse=True)
def override_dependencies():
    mock_session = MagicMock()
    user_id = uuid4()

    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: user_id

    yield mock_session, user_id

    app.dependency_overrides = {}


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
@patch(
    "flip_api.step_functions_services.approve_project_step_function.start_project_imaging_creation",
    new_callable=AsyncMock,
)
def test_approve_project_success(
    mock_start_imaging,
    mock_approve_project,
    project_id,
    request_body,
    mock_trusts,
):
    mock_approve_project.return_value = mock_trusts
    mock_start_imaging.return_value = None  # since it's async, just return None

    response = client.post(f"/step/project/{project_id}/approve", json=request_body)

    assert response.status_code == 200
    data = response.json()

    assert data["projectId"] == project_id
    assert data["successful"] is True
    assert data["trusts"]["processed"] == 2
    assert data["trusts"]["failed"] == 0
    assert data["trusts"]["succeeded"] == 2

    mock_approve_project.assert_called_once()
    assert mock_start_imaging.await_count == 2


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
@patch(
    "flip_api.step_functions_services.approve_project_step_function.start_project_imaging_creation",
    new_callable=AsyncMock,
)
def test_approve_project_with_failure_in_trust(
    mock_start_imaging,
    mock_approve_project,
    project_id,
    request_body,
    mock_trusts,
):
    # Simulate one trust failing
    async def failing_start(*args, **kwargs):
        trust = kwargs["trust"]
        if trust.name == "Trust 2":
            raise Exception("Imaging failed")
        return None

    mock_approve_project.return_value = mock_trusts
    mock_start_imaging.side_effect = failing_start

    response = client.post(f"/step/project/{project_id}/approve", json=request_body)

    assert response.status_code == 200
    data = response.json()

    assert data["successful"] is False
    assert data["trusts"]["processed"] == 2
    assert data["trusts"]["failed"] == 1
    assert data["trusts"]["succeeded"] == 1


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
def test_approve_project_with_empty_trusts(
    mock_approve_project,
    project_id,
    request_body,
    mock_trusts,
):
    mock_approve_project.return_value = []

    response = client.post(f"/step/project/{project_id}/approve", json=request_body)

    assert response.status_code == 200
    data = response.json()

    assert data["message"] == "Project approved but no trusts to process"
