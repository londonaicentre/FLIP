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
from fastapi.testclient import TestClient

# Set required environment variables before importing the app
# This must happen before fl_api.config is imported
os.environ.setdefault("FL_ADMIN_DIRECTORY", "/tmp/test_admin")

from fl_api.app import app
from fl_api.core.dependencies import get_session


@pytest.fixture
def mock_session():
    return MagicMock()


@pytest.fixture
def client(mock_session):
    with patch("fl_api.app.create_fl_session", return_value=mock_session):
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client


@pytest.fixture(autouse=True)
def override_session(client):
    from unittest.mock import MagicMock

    fake_session = MagicMock()
    app.dependency_overrides[get_session] = lambda: fake_session
    yield
    app.dependency_overrides.clear()
