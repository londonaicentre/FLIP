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
        TIMEOUT_SESSION_CONNECT = 5.0
        TIMEOUT_FL_SERVER_STARTUP = 600.0

    os.makedirs(Settings.FL_ADMIN_DIRECTORY, exist_ok=True)
    return Settings()


def test_create_fl_session_success(fake_settings):
    """✅ Should configure FLARE and return a session."""
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


def test_create_fl_session_refuses_a_gpu_default_no_job_could_use(fake_settings):
    """A bad JOB_RESOURCE_SPEC_NUM_GPUS default would fail every upload that falls back to it; fail at boot instead."""
    fake_settings.JOB_RESOURCE_SPEC_NUM_GPUS = 99

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.utils.job_resources.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.FLIP_Session") as session_cls,
        pytest.raises(ValueError, match="JOB_RESOURCE_SPEC"),
    ):
        create_fl_session()

    session_cls.assert_not_called()


def test_create_fl_session_waits_for_a_server_that_is_still_starting(fake_settings):
    """A slow FL server start is retried with a fresh session; each failed one is closed so its connector stops."""
    failed, ok = MagicMock(), MagicMock()
    failed.try_connect.side_effect = FLCommunicationError("cannot connect to server for 5.0 seconds")

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.FLIP_Session", side_effect=[failed, failed, ok]) as session_cls,
    ):
        assert create_fl_session() is ok

    assert session_cls.call_count == 3
    assert failed.close.call_count == 2
    ok.close.assert_not_called()


def test_create_fl_session_gives_up_after_the_startup_timeout(fake_settings):
    """Past TIMEOUT_FL_SERVER_STARTUP the connection error propagates, so the API does not start half-alive."""
    fake_settings.TIMEOUT_FL_SERVER_STARTUP = 0.0
    failed = MagicMock()
    failed.try_connect.side_effect = FLCommunicationError("cannot connect to server for 5.0 seconds")

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.FLIP_Session", return_value=failed),
        pytest.raises(FLCommunicationError),
    ):
        create_fl_session()

    failed.close.assert_called_once()
