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

import pytest

from tests.e2e.helpers import wait_for_service


@pytest.mark.e2e
class TestServiceHealthChecks:
    """Verify all platform services are reachable and healthy."""

    def test_central_hub_api_healthy(self, service_ports):
        """Central Hub API should respond on /health."""
        wait_for_service(f"http://localhost:{service_ports['api']}/health", timeout_s=60)

    def test_trust_api_healthy(self, service_ports):
        """Trust API should respond on /health."""
        wait_for_service(f"https://localhost:{service_ports['trust_api']}/health", timeout_s=60)

    def test_data_access_api_healthy(self, service_ports):
        """Data Access API should respond on /health."""
        wait_for_service(f"http://localhost:{service_ports['data_access_api']}/health", timeout_s=60)

    def test_imaging_api_healthy(self, service_ports):
        """Imaging API should respond on /health."""
        wait_for_service(f"http://localhost:{service_ports['imaging_api']}/health", timeout_s=60)

    def test_xnat_healthy(self, service_ports):
        """XNAT should respond on its web interface."""
        wait_for_service(f"http://localhost:{service_ports['xnat_trust_1']}", timeout_s=120)

    def test_central_hub_api_docs_accessible(self, service_ports):
        """Central Hub API docs should be accessible."""
        wait_for_service(f"http://localhost:{service_ports['api']}/docs", timeout_s=30)
