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

"""
Health check tests to verify all platform services are running before other E2E tests.
Must pass before any workflow tests can succeed.
"""

import os

import pytest

from tests.e2e.helpers import wait_for_service

# Port configuration - matches check_local_status.py and .env.development defaults
API_PORT = os.environ.get("API_PORT", "8001")
TRUST_API_PORT = os.environ.get("TRUST_API_PORT", "8100")
IMAGING_API_PORT = os.environ.get("IMAGING_API_PORT", "8200")
DATA_ACCESS_API_PORT = os.environ.get("DATA_ACCESS_API_PORT", "8300")
XNAT_PORT_TRUST_1 = os.environ.get("XNAT_PORT_TRUST_1", "8104")


@pytest.mark.e2e
class TestServiceHealthChecks:
    """Verify all platform services are reachable and healthy."""

    def test_central_hub_api_healthy(self):
        """Central Hub API should respond on /health."""
        wait_for_service(f"http://localhost:{API_PORT}/health", timeout_s=60)

    def test_trust_api_healthy(self):
        """Trust API should respond on /health."""
        wait_for_service(f"https://localhost:{TRUST_API_PORT}/health", timeout_s=60)

    def test_data_access_api_healthy(self):
        """Data Access API should respond on /health."""
        wait_for_service(f"http://localhost:{DATA_ACCESS_API_PORT}/health", timeout_s=60)

    def test_imaging_api_healthy(self):
        """Imaging API should respond on /health."""
        wait_for_service(f"http://localhost:{IMAGING_API_PORT}/health", timeout_s=60)

    def test_xnat_healthy(self):
        """XNAT should respond on its web interface."""
        wait_for_service(f"http://localhost:{XNAT_PORT_TRUST_1}", timeout_s=120)

    def test_central_hub_api_docs_accessible(self):
        """Central Hub API docs should be accessible."""
        wait_for_service(f"http://localhost:{API_PORT}/docs", timeout_s=30)
