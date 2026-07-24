# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

import os
from unittest.mock import MagicMock, patch

import pytest
from nvflare.apis.fl_exception import FLCommunicationError
from nvflare.fuel.flare_api.api_spec import AuthenticationError, NoConnection

from fl_api.startup.session_manager import create_fl_session


@pytest.fixture
def fake_settings(tmp_path):
    """Fake settings with a temporary admin directory."""

    class Settings:
        USERNAME = "admin@nvidia.com"
        SECURE_MODE = True
        LOG_LEVEL = "DEBUG"
        FL_ADMIN_DIRECTORY = str(tmp_path / "admin")
        JOB_RESOURCE_SPEC_NUM_GPUS = 2
        JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB = 8
        TIMEOUT_SESSION_CONNECT = 5.0
        PER_JOB_FL_SERVER = False

    os.makedirs(Settings.FL_ADMIN_DIRECTORY, exist_ok=True)
    return Settings()


def test_create_fl_session_success(fake_settings):
    """✅ Should configure NVFlare and return a session."""
    mock_session = MagicMock()
    mock_session.upload_dir = "/tmp/upload"
    mock_session.download_dir = "/tmp/download"

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.FLIP_Session", return_value=mock_session),
    ):
        session = create_fl_session()

    assert session == mock_session
    assert session.upload_dir == "/tmp/upload"
    assert session.download_dir == "/tmp/download"


def test_create_fl_session_raises_when_unreachable_and_flag_off(fake_settings):
    """❌ PER_JOB_FL_SERVER off (default): an unreachable server at boot still raises, unchanged."""
    fake_settings.PER_JOB_FL_SERVER = False
    mock_session = MagicMock()
    mock_session.try_connect.side_effect = NoConnection("cannot connect to server")

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.FLIP_Session", return_value=mock_session),
    ):
        with pytest.raises(NoConnection):
            create_fl_session()


def test_create_fl_session_tolerates_unreachable_server_when_flag_on(fake_settings):
    """✅ PER_JOB_FL_SERVER on: an unreachable server at boot is tolerated (idle-by-design, FLIP#735)."""
    fake_settings.PER_JOB_FL_SERVER = True
    mock_session = MagicMock()
    mock_session.upload_dir = "/tmp/upload"
    mock_session.download_dir = "/tmp/download"
    mock_session.try_connect.side_effect = NoConnection("cannot connect to server")

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.FLIP_Session", return_value=mock_session),
    ):
        session = create_fl_session()

    assert session == mock_session


def test_create_fl_session_tolerates_fl_communication_error_when_flag_on(fake_settings):
    """✅ PER_JOB_FL_SERVER on: a raw FLCommunicationError (not remapped to NoConnection) is tolerated too."""
    fake_settings.PER_JOB_FL_SERVER = True
    mock_session = MagicMock()
    mock_session.try_connect.side_effect = FLCommunicationError("comm error")

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.FLIP_Session", return_value=mock_session),
    ):
        session = create_fl_session()

    assert session == mock_session


def test_create_fl_session_still_raises_on_non_connectivity_error_when_flag_on(fake_settings):
    """❌ PER_JOB_FL_SERVER on: a non-connectivity failure (e.g. auth) still raises — only
    unreachability is tolerated, not arbitrary startup failures."""
    fake_settings.PER_JOB_FL_SERVER = True
    mock_session = MagicMock()
    mock_session.try_connect.side_effect = AuthenticationError("bad cert")

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.FLIP_Session", return_value=mock_session),
    ):
        with pytest.raises(AuthenticationError):
            create_fl_session()
