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

from unittest.mock import Mock

import pytest
from sqlmodel import Session

from flip_api.db.models.main_models import SiteConfig
from flip_api.db.seed.site_config import seed_config


@pytest.fixture
def mock_session():
    """Create a mock database session."""
    return Mock(spec=Session)


def test_seed_config_adds_deployment_mode_off_when_missing(mock_session):
    mock_session.exec.return_value.first.return_value = None

    seed_config(mock_session)

    added = mock_session.add.call_args[0][0]
    assert isinstance(added, SiteConfig)
    assert added.key == "DeploymentMode"
    assert added.value is False
    mock_session.commit.assert_called_once()


def test_seed_config_leaves_an_existing_row_alone(mock_session):
    """A hub left in Deployment Mode must stay in it across a restart."""
    mock_session.exec.return_value.first.return_value = SiteConfig(key="DeploymentMode", value=True)

    seed_config(mock_session)

    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()
