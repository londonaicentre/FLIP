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

from uuid import uuid4

import pytest

from flip_api.config import get_settings
from flip_api.db.models.main_models import Model, Projects
from tests.integration.utils import admin_authentication


@pytest.fixture
def admin_auth_token():
    """
    Use boto3 to get a valid admin token for testing.
    This fixture assumes you have AWS credentials configured in your environment.
    """
    return admin_authentication()


def test_fetch_project_data(create_project_data, session):
    """Test fetching project data."""
    project = create_project_data
    fetched_project = session.get(Projects, project.id)
    assert fetched_project is not None, "Project should exist in the database"
    assert fetched_project.name == project.name
    assert fetched_project.description == project.description


def test_get_presigned_url_success(real_client, session, create_model_data, admin_auth_token):
    """Test successfully getting a presigned URL for a valid model."""
    model_id = create_model_data.id
    # Verify the model exists
    model = session.get(Model, model_id)
    assert model is not None, "Model should exist in the database"
    # Verify the project exists
    project = session.get(Projects, model.project_id)
    assert project is not None, "Project should exist in the database"
    # Request a presigned URL for the model
    response = real_client.post(
        f"{get_settings().FLIP_API_URL}/files/preSignedUrl/model/{model_id}",
        json={"fileName": "some_file.txt"},
        headers=admin_auth_token,
    )
    assert response.status_code == 200, f"Unexpected status code: {response.status_code}"


def test_get_presigned_url_invalid_model(real_client, admin_auth_token):
    """Test requesting a presigned URL for a model that does not exist."""
    fake_model_id = str(uuid4())  # An ID not present in the database
    response = real_client.post(
        f"{get_settings().FLIP_API_URL}/files/preSignedUrl/model/{fake_model_id}",
        json={"fileName": "does_not_exist.txt"},
        headers=admin_auth_token,
    )
    assert response.status_code == 404, f"Expected 404 if model not found, got {response.status_code}"
    assert "does not exist or is deleted" in response.text
