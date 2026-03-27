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
from uuid import uuid4

import pytest

from imaging_api.routers.schemas import CentralHubProject, Project


@pytest.fixture
def mock_headers():
    return {"Cookie": "JSESSIONID=mock"}


TEST_XNAT_PROJECT_ID = "dc5c1758-1a4d-4fca-80ce-fa208d11874d"


@pytest.fixture
def mock_project():
    return Project(
        ID=TEST_XNAT_PROJECT_ID,
        secondary_ID="TEST",
        name="Test Project",
        description="A test project",
        pi_firstname="John",
        pi_lastname="Doe",
        URI=f"/projects/{TEST_XNAT_PROJECT_ID}",
    )


@pytest.fixture
def central_hub_project_nifti_enabled():
    return CentralHubProject(
        project_id=uuid4(),
        trust_id=uuid4(),
        project_name="Test Project",
        query="SELECT * FROM studies",
        users=[],
        dicom_to_nifti=True,
    )


@pytest.fixture
def central_hub_project_nifti_disabled():
    return CentralHubProject(
        project_id=uuid4(),
        trust_id=uuid4(),
        project_name="Test Project",
        query="SELECT * FROM studies",
        users=[],
        dicom_to_nifti=False,
    )


@pytest.mark.asyncio
@patch("imaging_api.routers.projects.retrieve_images_for_project")
@patch("imaging_api.routers.projects.add_central_hub_users_to_project")
@patch("imaging_api.routers.projects.create_project_event_subscription")
@patch("imaging_api.routers.projects.set_project_prearchive_settings")
@patch("imaging_api.routers.projects.create_project")
@patch("imaging_api.routers.projects.to_create_project")
@patch("imaging_api.routers.projects.get_xnat_auth_headers")
async def test_create_project_nifti_enabled_creates_active_event_subscription(
    mock_auth,
    mock_to_create,
    mock_create,
    mock_prearchive,
    mock_event_sub,
    mock_add_users,
    mock_retrieve,
    mock_project,
    central_hub_project_nifti_enabled,
    mock_headers,
):
    """When dicom_to_nifti is True, create_project_event_subscription should be called with active=True."""
    mock_to_create.return_value = MagicMock(id="TEST", secondary_id="TEST", name="Test", description="")
    mock_create.return_value = mock_project
    mock_add_users.return_value = ([], [])

    from imaging_api.routers.projects import create_project_from_central_hub_project

    background_tasks = MagicMock()
    await create_project_from_central_hub_project(central_hub_project_nifti_enabled, mock_headers, background_tasks)

    mock_event_sub.assert_called_once_with(
        TEST_XNAT_PROJECT_ID, "xnat/dcm2niix:latest", True, mock_headers
    )


@pytest.mark.asyncio
@patch("imaging_api.routers.projects.retrieve_images_for_project")
@patch("imaging_api.routers.projects.add_central_hub_users_to_project")
@patch("imaging_api.routers.projects.create_project_event_subscription")
@patch("imaging_api.routers.projects.set_project_prearchive_settings")
@patch("imaging_api.routers.projects.create_project")
@patch("imaging_api.routers.projects.to_create_project")
@patch("imaging_api.routers.projects.get_xnat_auth_headers")
async def test_create_project_nifti_disabled_creates_inactive_event_subscription(
    mock_auth,
    mock_to_create,
    mock_create,
    mock_prearchive,
    mock_event_sub,
    mock_add_users,
    mock_retrieve,
    mock_project,
    central_hub_project_nifti_disabled,
    mock_headers,
):
    """When dicom_to_nifti is False, create_project_event_subscription should be called with active=False."""
    mock_to_create.return_value = MagicMock(id="TEST", secondary_id="TEST", name="Test", description="")
    mock_create.return_value = mock_project
    mock_add_users.return_value = ([], [])

    from imaging_api.routers.projects import create_project_from_central_hub_project

    background_tasks = MagicMock()
    await create_project_from_central_hub_project(central_hub_project_nifti_disabled, mock_headers, background_tasks)

    mock_event_sub.assert_called_once_with(
        TEST_XNAT_PROJECT_ID, "xnat/dcm2niix:latest", False, mock_headers
    )
