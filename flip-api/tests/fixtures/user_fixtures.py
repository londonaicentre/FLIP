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

import uuid
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_request():
    """Mock FastAPI request object."""
    request = MagicMock()
    return request


@pytest.fixture
def user_id():
    """Valid UUID user ID fixture."""
    return str(uuid.uuid4())


@pytest.fixture
def user_email():
    """Valid email user ID fixture."""
    return "test.user@example.com"


@pytest.fixture
def user_data():
    """Sample user data returned from Cognito."""
    return {
        "id": str(uuid.uuid4()),
        "email": "test.user@example.com",
        "is_disabled": False,
        "roles": [{"rolename": "Researcher", "roledescription": "A researcher"}],
    }
