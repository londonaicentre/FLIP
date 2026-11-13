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

import pytest
from sqlmodel import Session

from flip_api.db.models.main_models import SiteConfig
from flip_api.utils.site_manager import is_deployment_mode_enabled


@pytest.mark.parametrize(
    ("row", "expected"),
    [(SiteConfig(key="DeploymentMode", value=True), True), (None, False)],  # None: no matching row
    ids=["enabled", "disabled"],
)
def test_deployment_mode(row, expected):
    mock_session = MagicMock(spec=Session)
    mock_session.exec.return_value.first.return_value = row

    assert is_deployment_mode_enabled(mock_session) is expected
    mock_session.exec.assert_called_once()
