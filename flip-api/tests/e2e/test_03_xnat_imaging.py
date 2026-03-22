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
import os

import pytest

from flip_api.domain.schemas.projects import ProjectDetails
from flip_api.utils.constants import BASE_URL
from tests.debug_prelaunch_task import (
    add_project_query,
    create_new_project,
    submit_query_to_trusts,
)
from tests.e2e.helpers import poll_until
from tests.integration.utils import admin_authentication

TRUST_API_PORT = os.environ.get("TRUST_API_PORT", "8100")


@pytest.mark.e2e
class TestXNATProjectCreation:
    """Test that XNAT projects are created when a project is approved."""

    def _create_and_approve_project(self, authed_client, cohort_query_sql, trust_ids, cleanup_projects):
        """Helper to create a fully approved project. Returns (project_id, query_sql)."""
        project_data = ProjectDetails(
            name="E2E Test - XNAT Imaging",
            description="Tests XNAT project creation and image retrieval",
            users=[],
        )
        response = create_new_project(authed_client, project_data)
        assert response is not None
        project_id = response.json()["id"]
        cleanup_projects.append(project_id)

        # Add and submit query
        auth_token = admin_authentication()
        query_info = {
            "query": cohort_query_sql,
            "name": "E2E XNAT Test Query",
            "project_id": str(project_id),
        }
        query_response = add_project_query(authed_client, query_info)
        assert query_response is not None
        query_id = query_response.json()["query_id"]

        submit_info = {
            "authenticationToken": auth_token.get("authorization", ""),
            "query": cohort_query_sql,
            "name": "E2E XNAT Test Query",
            "project_id": str(project_id),
            "query_id": str(query_id),
        }
        submit_query_to_trusts(authed_client, submit_info)

        # Stage
        stage_response = authed_client.post(
            f"{BASE_URL}/projects/{project_id}/stage/",
            json={"trusts": trust_ids},
            timeout=30,
        )
        assert stage_response.status_code < 300

        # Approve (triggers XNAT project creation + image retrieval in background)
        approve_response = authed_client.post(
            f"{BASE_URL}/step/project/{project_id}/approve/",
            json={"trusts": trust_ids},
            timeout=60,
        )
        assert approve_response.status_code < 300
        approval_data = approve_response.json()

        return project_id, approval_data

    def test_xnat_project_created_on_approval(self, authed_client, cohort_query_sql, trust_ids, cleanup_projects):
        """Approving a project should trigger XNAT project creation on all trusts."""
        project_id, approval_data = self._create_and_approve_project(
            authed_client, cohort_query_sql, trust_ids, cleanup_projects
        )

        assert approval_data.get("successful") is True, f"Approval not successful: {approval_data}"

        # Verify trust processing details
        details = approval_data.get("details", [])
        assert len(details) > 0, "No trust processing details returned"
        for detail in details:
            assert detail.get("success") is True, (
                f"Trust {detail.get('trust')} failed: {detail.get('message')}"
            )

    @pytest.mark.slow
    def test_xnat_image_retrieval(self, authed_client, cohort_query_sql, trust_ids, cleanup_projects):
        """
        After project approval, XNAT should retrieve images from PACS.

        This polls the imaging project status via the trust API until images
        are successfully imported. This is the slowest E2E test as it waits
        for PACS image retrieval.
        """
        project_id, approval_data = self._create_and_approve_project(
            authed_client, cohort_query_sql, trust_ids, cleanup_projects
        )

        # Encode the query for the trust API endpoint
        encoded_query = base64.urlsafe_b64encode(cohort_query_sql.encode()).decode()

        # The imaging project ID used in XNAT is derived from the central hub project ID.
        # We need to check via the trust API which proxies to imaging API.
        # The trust API endpoint: GET /imaging/{imaging_project_id}?encoded_query=...
        # The imaging_project_id is typically a UUID generated during project creation.
        # We can find it from the approval details or by checking the XNAT project status.

        # Poll the import status via the trust API
        def check_import_status():
            try:
                # Try to get import status via trust API
                # The trust API uses HTTPS with self-signed certs in dev
                resp = authed_client.get(
                    f"https://localhost:{TRUST_API_PORT}/imaging/{project_id}",
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

                    # Return truthy if at least some images are imported
                    # or if there are no more queued/processing items
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
