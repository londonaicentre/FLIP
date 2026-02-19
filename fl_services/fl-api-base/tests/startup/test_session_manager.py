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
from fastapi import HTTPException
from nvflare.fuel.common.excepts import ConfigError

from fl_api.startup.session_manager import create_fl_session


@pytest.fixture
def fake_settings(tmp_path):
    """Fake settings with a temporary admin directory."""

    class Settings:
        LOG_LEVEL = "DEBUG"
        FL_ADMIN_DIRECTORY = str(tmp_path / "admin")
        JOB_RESOURCE_SPEC_NUM_GPUS = 2
        JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB = 8

    os.makedirs(Settings.FL_ADMIN_DIRECTORY, exist_ok=True)
    return Settings()


def test_create_fl_session_success(fake_settings):
    """✅ Should configure NVFlare and return a session."""
    mock_workspace = MagicMock()
    mock_conf = MagicMock()
    mock_conf.config_data = {"admin": {"upload_dir": "/tmp/upload", "download_dir": "/tmp/download"}}
    mock_session = MagicMock()

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.os.chdir"),
        patch("fl_api.startup.session_manager.Workspace", return_value=mock_workspace) as ws_cls,
        patch("fl_api.startup.session_manager.secure_load_admin_config", return_value=mock_conf) as conf_func,
        patch("fl_api.startup.session_manager.new_secure_Flip_session", return_value=mock_session),
        patch("fl_api.startup.session_manager.os.path.isdir", return_value=True),
        patch("fl_api.startup.session_manager.os.makedirs") as makedirs,
    ):
        session = create_fl_session()

    ws_cls.assert_called_once_with(root_dir=fake_settings.FL_ADMIN_DIRECTORY)
    conf_func.assert_called_once_with(mock_workspace)
    assert session == mock_session
    makedirs.assert_not_called()


def test_create_fl_session_creates_missing_download_dir(fake_settings):
    """✅ Should create download_dir when missing."""
    mock_workspace = MagicMock()
    mock_conf = MagicMock()
    mock_conf.config_data = {"admin": {"upload_dir": "/tmp/upload", "download_dir": "/tmp/download"}}

    created_dirs = []

    def fake_makedirs(path):
        created_dirs.append(path)

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.os.chdir"),
        patch("fl_api.startup.session_manager.Workspace", return_value=mock_workspace),
        patch("fl_api.startup.session_manager.secure_load_admin_config", return_value=mock_conf),
        patch("fl_api.startup.session_manager.new_secure_Flip_session", return_value=MagicMock()),
        patch("fl_api.startup.session_manager.os.path.isdir", side_effect=lambda p: p != "/tmp/download"),
        patch("fl_api.startup.session_manager.os.makedirs", side_effect=fake_makedirs),
    ):
        create_fl_session()

    assert "/tmp/download" in created_dirs


def test_create_fl_session_config_error(fake_settings):
    """❌ Should raise HTTP 500 if ConfigError is raised."""
    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.os.chdir"),
        patch("fl_api.startup.session_manager.Workspace", return_value=MagicMock()),
        patch("fl_api.startup.session_manager.secure_load_admin_config", side_effect=ConfigError("bad config")),
    ):
        with pytest.raises(HTTPException) as exc:
            create_fl_session()

    assert exc.value.status_code == 500
    assert "ConfigError" in exc.value.detail


def test_create_fl_session_unexpected_exception(fake_settings):
    """❌ Should raise HTTP 500 on unexpected exception."""
    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.os.chdir", side_effect=RuntimeError("boom")),
    ):
        with pytest.raises(HTTPException) as exc:
            create_fl_session()

    assert exc.value.status_code == 500
    assert "Unexpected error" in exc.value.detail


def test_create_fl_session_missing_admin_section(fake_settings):
    """❌ Should raise HTTP 500 if admin section missing in config."""
    mock_conf = MagicMock()
    mock_conf.config_data = {}

    with (
        patch("fl_api.startup.session_manager.get_settings", return_value=fake_settings),
        patch("fl_api.startup.session_manager.os.chdir"),
        patch("fl_api.startup.session_manager.Workspace", return_value=MagicMock()),
        patch("fl_api.startup.session_manager.secure_load_admin_config", return_value=mock_conf),
    ):
        with pytest.raises(HTTPException) as exc:
            create_fl_session()

    assert exc.value.status_code == 500
    assert "Missing 'admin' section" in exc.value.detail
