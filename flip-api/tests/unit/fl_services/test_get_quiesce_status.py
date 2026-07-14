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

from flip_api.domain.schemas.status import NetStatus
from flip_api.fl_services.get_quiesce_status import get_quiesce_status


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture(autouse=True)
def deployment_mode_disabled():
    with patch(
        "flip_api.fl_services.get_quiesce_status.is_deployment_mode_enabled", return_value=False
    ) as mock_enabled:
        yield mock_enabled


def _scheduler_row(status: NetStatus) -> MagicMock:
    return MagicMock(status=status)


def test_quiesce_status_all_available(mock_db, deployment_mode_disabled):
    """No BUSY scheduler → fl_quiesced True; deployment_mode passed through as False."""
    mock_db.exec.return_value.all.return_value = [
        (_scheduler_row(NetStatus.AVAILABLE), "net-1"),
        (_scheduler_row(NetStatus.AVAILABLE), "net-2"),
    ]

    result = get_quiesce_status(mock_db, user_id=uuid4())

    assert result.deployment_mode is False
    assert result.fl_quiesced is True
    assert [s.net for s in result.schedulers] == ["net-1", "net-2"]
    assert all(s.status == NetStatus.AVAILABLE for s in result.schedulers)


def test_quiesce_status_busy_net_blocks_quiesce(mock_db, deployment_mode_disabled):
    """Any BUSY scheduler → fl_quiesced False, even with deployment mode enabled."""
    deployment_mode_disabled.return_value = True
    mock_db.exec.return_value.all.return_value = [
        (_scheduler_row(NetStatus.AVAILABLE), "net-1"),
        (_scheduler_row(NetStatus.BUSY), "net-2"),
    ]

    result = get_quiesce_status(mock_db, user_id=uuid4())

    assert result.deployment_mode is True
    assert result.fl_quiesced is False
    busy = [s for s in result.schedulers if s.status == NetStatus.BUSY]
    assert [s.net for s in busy] == ["net-2"]


def test_quiesce_status_ready_to_deploy(mock_db, deployment_mode_disabled):
    """Deployment mode on + nothing BUSY → both flags true (the deploy pre-flight condition)."""
    deployment_mode_disabled.return_value = True
    mock_db.exec.return_value.all.return_value = [(_scheduler_row(NetStatus.AVAILABLE), "net-1")]

    result = get_quiesce_status(mock_db, user_id=uuid4())

    assert result.deployment_mode is True
    assert result.fl_quiesced is True
