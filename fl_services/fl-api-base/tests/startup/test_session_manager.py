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

import os
from unittest.mock import MagicMock, patch

import pytest

from fl_api.startup.session_manager import create_fl_session


@pytest.fixture
def fake_settings(tmp_path):
    """Fake settings with a temporary admin directory."""

    class Settings:
        LOG_LEVEL = "DEBUG"
        FL_ADMIN_DIRECTORY = str(tmp_path / "admin")
        JOB_RESOURCE_SPEC_NUM_GPUS = 2
        JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB = 8
        TIMEOUT_SESSION_CONNECT = 5.0

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
