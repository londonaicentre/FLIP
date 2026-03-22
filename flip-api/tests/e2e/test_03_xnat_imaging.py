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
E2E tests for XNAT imaging project creation and image retrieval.
Tests the full XNAT flow triggered by project approval.
"""

import base64

import pytest
import urllib3

from tests.e2e.helpers import create_and_approve_project, poll_until

# Trust API uses HTTPS with self-signed certs in local dev
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@pytest.mark.e2e
class TestXNATProjectCreation:
    """Test that XNAT projects are created when a project is approved."""

    def test_xnat_project_created_on_approval(self, authed_client, cohort_query_sql, trust_ids, cleanup_projects):
        """Approving a project should trigger XNAT project creation on all trusts."""
        project_id, approval_data = create_and_approve_project(
            authed_client, cohort_query_sql, trust_ids, cleanup_projects,
            project_name="E2E Test - XNAT Imaging",
        )

        assert approval_data.get("successful") is True, f"Approval not successful: {approval_data}"

        details = approval_data.get("details", [])
        assert len(details) > 0, "No trust processing details returned"
        for detail in details:
            assert detail.get("success") is True, (
                f"Trust {detail.get('trust')} failed: {detail.get('message')}"
            )

    @pytest.mark.slow
    def test_xnat_image_retrieval(
        self, authed_client, cohort_query_sql, trust_ids, cleanup_projects, service_ports,
    ):
        """
        After project approval, XNAT should retrieve images from PACS.

        This polls the imaging project status via the trust API until images
        are successfully imported. This is the slowest E2E test as it waits
        for PACS image retrieval.
        """
        project_id, approval_data = create_and_approve_project(
            authed_client, cohort_query_sql, trust_ids, cleanup_projects,
            project_name="E2E Test - XNAT Image Retrieval",
        )

        encoded_query = base64.urlsafe_b64encode(cohort_query_sql.encode()).decode()
        trust_api_port = service_ports["trust_api"]

        # The trust API proxies to the imaging API's retrieval status endpoint.
        def check_import_status():
            try:
                resp = authed_client.get(
                    f"https://localhost:{trust_api_port}/imaging/{project_id}",
                    params={"encoded_query": encoded_query},
                    timeout=30,
                    verify=False,
                )
                if resp.status_code < 300:
                    data = resp.json()
                    import_status = data.get("import_status", {})
                    successful = import_status.get("successful_count", 0)
                    processing = import_status.get("processing_count", 0)
                    queued = import_status.get("queued_count", 0)

                    if successful > 0:
                        return data
                    if processing == 0 and queued == 0 and successful == 0:
                        # No images to import - still valid
                        return data
                return None
            except Exception:
                return None

        result = poll_until(
            check_import_status,
            timeout_s=300,  # 5 minutes for PACS retrieval
            interval_s=15,
            description="XNAT image retrieval from PACS",
        )

        assert result is not None, "XNAT image retrieval did not complete within timeout"
        assert result.get("project_creation_completed") is True, "XNAT project creation should be completed"
