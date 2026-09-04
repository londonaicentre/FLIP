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
from nvflare.fuel.flare_api.api_spec import InternalError, NoConnection

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


def test_create_fl_session_tolerates_unreachable_server_when_flag_on(fake_settings):
    """✅ PER_JOB_FL_SERVER on: transport-down at boot is tolerated (lazy connect later)."""
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


def test_create_fl_session_raises_unreachable_when_flag_off(fake_settings):
    """✅ PER_JOB_FL_SERVER off (default): transport-down at boot stays fatal."""
    mock_session = MagicMock()
    mock_session.try_connect.side_effect = NoConnection("cannot connect to server")

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.FLIP_Session", return_value=mock_session),
    ):
        with pytest.raises(NoConnection):
            create_fl_session()


def test_create_fl_session_tolerates_internal_error_when_flag_on(fake_settings):
    """✅ PER_JOB_FL_SERVER on: login-failed InternalError (server up, not ready) tolerated."""
    fake_settings.PER_JOB_FL_SERVER = True
    mock_session = MagicMock()
    mock_session.upload_dir = "/tmp/upload"
    mock_session.download_dir = "/tmp/download"
    mock_session.try_connect.side_effect = InternalError("login failed: ERROR_RUNTIME")

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.FLIP_Session", return_value=mock_session),
    ):
        session = create_fl_session()

    assert session == mock_session


def test_create_fl_session_raises_internal_error_when_flag_off(fake_settings):
    """✅ PER_JOB_FL_SERVER off: login-failed InternalError at boot stays fatal."""
    mock_session = MagicMock()
    mock_session.try_connect.side_effect = InternalError("login failed: ERROR_RUNTIME")

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.FLIP_Session", return_value=mock_session),
    ):
        with pytest.raises(InternalError):
            create_fl_session()


def test_create_fl_session_raises_on_cannot_authenticate_even_when_flag_on(fake_settings):
    """✅ PER_JOB_FL_SERVER on: NoConnection("cannot authenticate") is identity, always fatal."""
    fake_settings.PER_JOB_FL_SERVER = True
    mock_session = MagicMock()
    mock_session.try_connect.side_effect = NoConnection("cannot authenticate to server")

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.FLIP_Session", return_value=mock_session),
    ):
        with pytest.raises(NoConnection):
            create_fl_session()


def test_create_fl_session_raises_on_identity_mismatch_even_when_flag_on(fake_settings):
    """✅ PER_JOB_FL_SERVER on: identity mismatch is a misconfiguration, always fatal."""
    fake_settings.PER_JOB_FL_SERVER = True
    mock_session = MagicMock()
    mock_session.try_connect.side_effect = FLCommunicationError("rejected registration")

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.FLIP_Session", return_value=mock_session),
    ):
        with pytest.raises(FLCommunicationError):
            create_fl_session()
