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
from fastapi import HTTPException

from flip_api.project_services.reimport_imaging_project_studies import reimport_imaging_project_studies


@pytest.fixture
def mock_session():
    return MagicMock()


@patch("flip.project_services.reimport_imaging_project_studies.get_settings")
@patch("flip.project_services.reimport_imaging_project_studies.get_reimport_queries_service")
@patch("flip.project_services.reimport_imaging_project_studies.reimport_failed_studies")
def test_reimport_success(mock_reimport, mock_queries_service, mock_get_settings, mock_session):
    # Mock settings
    mock_get_settings.return_value.PROJECT_REIMPORT_RATE = 2
    mock_get_settings.return_value.MAX_REIMPORT_COUNT = 100

    # Mock returned reimport queries
    mock_queries_service.return_value = ["query1", "query2"]
    mock_reimport.return_value = True

    # Call the function
    result = reimport_imaging_project_studies(mock_session)

    # Ensure it returns None (i.e., completed without exception)
    assert result is None
    mock_reimport.assert_called_once()


@patch("flip.project_services.reimport_imaging_project_studies.get_settings")
@patch("flip.project_services.reimport_imaging_project_studies.get_reimport_queries_service")
@patch("flip.project_services.reimport_imaging_project_studies.reimport_failed_studies")
def test_failed_study_reimport_raises_500(mock_reimport, mock_queries_service, mock_get_settings, mock_session):
    mock_get_settings.return_value.PROJECT_REIMPORT_RATE = 2
    mock_get_settings.return_value.MAX_REIMPORT_COUNT = 100

    mock_queries_service.return_value = ["query1"]
    mock_reimport.return_value = False

    with pytest.raises(HTTPException) as exc_info:
        reimport_imaging_project_studies(mock_session)

    assert exc_info.value.status_code == 500
    assert "error occurred while reimporting" in exc_info.value.detail
