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

from unittest.mock import MagicMock

from sqlmodel import Session

from flip_api.db.models.main_models import SiteConfig
from flip_api.utils.site_manager import is_deployment_mode_enabled


def test_deployment_mode_enabled():
    # Arrange
    mock_session = MagicMock(spec=Session)
    mock_exec = mock_session.exec
    mock_result = MagicMock()
    mock_result.first.return_value = SiteConfig(key="DeploymentMode", value=True)
    mock_exec.return_value = mock_result

    # Act
    result = is_deployment_mode_enabled(mock_session)

    # Assert
    assert result is True
    mock_exec.assert_called_once()


def test_deployment_mode_disabled():
    # Arrange
    mock_session = MagicMock(spec=Session)
    mock_exec = mock_session.exec
    mock_result = MagicMock()
    mock_result.first.return_value = None  # Simulate no matching row
    mock_exec.return_value = mock_result

    # Act
    result = is_deployment_mode_enabled(mock_session)

    # Assert
    assert result is False
    mock_exec.assert_called_once()
