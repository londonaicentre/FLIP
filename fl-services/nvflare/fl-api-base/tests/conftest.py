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

import ipaddress
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Set required environment variables before importing the app
# This must happen before fl_api.config is imported
os.environ.setdefault("FL_ADMIN_DIRECTORY", "/tmp/test_admin")

from fl_api.app import app
from fl_api.core.dependencies import get_session
from fl_api.utils import validation


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


# A public address for the stub resolver below: what an S3 endpoint answers. Deliberately not a TEST-NET
# address — those sit in the IANA special-purpose registry and ``ipaddress`` flags them private, which is
# exactly the rejection the stub must never trigger.
STUB_PUBLIC_ADDRESS = ipaddress.ip_address("52.95.150.1")


@pytest.fixture(autouse=True)
def stub_public_resolver(monkeypatch):
    """Resolve every DNS name to one public address, so no test performs a real lookup (FLIP#905).

    ``validate_bundle_url`` resolves DNS names through ``validation.resolve_bundle_host`` and fails closed on
    a resolver error, so the non-resolving ``test.local`` / ``example.com`` bundle URLs used across the suite
    would otherwise be rejected by name resolution — or, worse, depend on the runner's DNS. Tests that need
    a specific answer patch the same seam again. The once-per-process empty-allow-list warning flag is reset
    alongside so the tests of that warning observe its first emission.

    Returns:
        Callable: The real ``resolve_bundle_host`` this fixture displaced, for the one test that exercises it
        against a patched ``socket.getaddrinfo``.
    """
    real_resolve_bundle_host = validation.resolve_bundle_host
    monkeypatch.setattr(validation, "resolve_bundle_host", lambda hostname: [STUB_PUBLIC_ADDRESS])
    monkeypatch.setattr(validation, "_warned_empty_allow_list", False)
    return real_resolve_bundle_host
