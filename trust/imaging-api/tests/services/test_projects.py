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

from unittest.mock import MagicMock, patch

import pytest

from imaging_api.db.models import QueuedPacsRequest
from imaging_api.routers.schemas import Project
from imaging_api.services.projects import create_project, delete_project, delete_queued_import_requests


@pytest.fixture
def headers():
    return {}


@patch("imaging_api.services.projects.get_project")
@patch("imaging_api.services.projects.get_all_projects")
@patch("imaging_api.services.projects.requests.post")
def test_create_project(mock_post, mock_get_all_projects, mock_get_project, headers):

    # Configure the mock for requests.post
    # This simulates a successful POST request to create a project
    # and returns a mock response with status code 200
    mock_post.return_value = MagicMock(status_code=200)

    # Mock get_all_projects to return an empty list
    # This simulates that there are no existing projects
    # and allows the create_project function to proceed
    mock_get_all_projects.return_value = []

    # Configure the mock for get_project
    mock_get_project.return_value = Project(
        ID="TEST",
        secondary_ID="TEST",
        name="Test Project",
        description="A test project",
        pi_firstname="John",
        pi_lastname="Doe",
        URI="/projects/TEST",
    )

    project_id = "TEST"
    project_secondary_id = "TEST"
    project_name = "Test Project"
    project_description = "A test project"
    project = create_project(project_id, project_secondary_id, project_name, project_description, headers)
    assert project.ID == project_id


@pytest.mark.asyncio
@patch("imaging_api.services.projects.delete_queued_import_requests")
@patch("imaging_api.services.projects.get_project")
@patch("imaging_api.services.projects.requests.delete")
async def test_delete_project(mock_delete, mock_get_project, mock_delete_queued_import_requests, headers):

    # Configure the mock for requests.delete
    # This simulates a successful DELETE request to delete a project
    # and returns a mock response with status code 200
    mock_delete.return_value = MagicMock(status_code=200)

    # Mock the get_project function to return a project object
    mock_get_project.return_value = Project(
        ID="TEST",
        secondary_ID="TEST",
        name="Test Project",
        description="A test project",
        pi_firstname="John",
        pi_lastname="Doe",
        URI="/projects/TEST",
    )

    # Mock the delete_queued_import_requests function to return True
    # This simulates that the queued import requests were deleted successfully
    mock_delete_queued_import_requests.return_value = True

    project_id = "TEST"
    project = await delete_project(project_id, headers)
    assert project.ID == project_id


@pytest.mark.asyncio
@patch("imaging_api.services.projects.get_queued_pacs_request_by_project")
@patch("imaging_api.services.projects.requests.post")
async def test_delete_queued_import_requests(mock_post, mock_get_queued_pacs_request_by_project, headers):

    # Mock the response of get_queued_pacs_request_by_project
    mock_get_queued_pacs_request_by_project.return_value = [
        QueuedPacsRequest(
            id=1,
            created="2023-10-01T00:00:00",
            accession_number="FAK57777617",
            status="QUEUED",
            xnat_project="TEST",
        )
    ]

    # Mock the response of requests.post
    mock_post.return_value = MagicMock(status_code=200)

    project_id = "TEST"
    result = await delete_queued_import_requests(project_id, headers)
    assert result is True
