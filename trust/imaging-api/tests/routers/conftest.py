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

import pytest
from fastapi.testclient import TestClient

from imaging_api.main import app
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.internal_auth import authenticate_internal_service


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def override_auth_headers():
    # Bypass both the XNAT cookie fetch and the trust-internal service key check;
    # router behaviour is the unit under test here, not the auth layer (see
    # tests/utils/test_internal_auth.py for direct coverage of the auth dependency).
    app.dependency_overrides[get_xnat_auth_headers] = lambda: {"Cookie": "JSESSIONID=fake"}
    app.dependency_overrides[authenticate_internal_service] = lambda: None
    yield
    app.dependency_overrides.clear()
