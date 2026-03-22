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
E2E tests for the project lifecycle: create -> add query -> stage -> approve.
Reuses helpers from tests/debug_prelaunch_task.py.
"""

import pytest

from flip_api.domain.schemas.projects import ProjectDetails
from flip_api.utils.constants import BASE_URL
from tests.debug_prelaunch_task import (
    add_project_query,
    create_new_project,
)
from tests.e2e.helpers import create_and_approve_project, create_and_submit_project


@pytest.mark.e2e
class TestProjectLifecycle:
    """Test the complete project lifecycle from creation to approval."""

    def test_create_project(self, authed_client, cleanup_projects):
        """A new project can be created via the API."""
        project_data = ProjectDetails(
            name="E2E Test - Create Project",
            description="Automated E2E test project",
            users=[],
        )

        response = authed_client.post(
            f"{BASE_URL}/projects/",
            json=project_data.model_dump(),
            timeout=30,
        )

        assert response.status_code < 300, f"Failed to create project: {response.status_code} {response.text}"
        data = response.json()
        assert "id" in data, "Response should contain project ID"
        cleanup_projects.append(data["id"])

    def test_create_project_with_cohort_query(self, authed_client, cohort_query_sql, cleanup_projects):
        """A cohort query can be saved and submitted for a project."""
        project_data = ProjectDetails(
            name="E2E Test - Project with Query",
            description="Automated E2E test with cohort query",
            users=[],
        )
        response = create_new_project(authed_client, project_data)
        assert response is not None, "Failed to create project"
        assert response.status_code < 300, f"Create project failed: {response.status_code}"
        project_id = response.json()["id"]
        cleanup_projects.append(project_id)

        query_info = {
            "query": cohort_query_sql,
            "name": "E2E Test Cohort Query",
            "project_id": str(project_id),
        }
        query_response = add_project_query(authed_client, query_info)
        assert query_response is not None, "Failed to save cohort query"
        assert query_response.status_code < 300, f"Save query failed: {query_response.status_code}"
        assert "query_id" in query_response.json(), "Response should contain query_id"

    def test_stage_project(self, authed_client, cohort_query_sql, trust_ids, cleanup_projects):
        """A project can be staged after adding a query."""
        project_id, _ = create_and_submit_project(
            authed_client, cohort_query_sql, cleanup_projects,
            project_name="E2E Test - Stage Project",
        )

        stage_response = authed_client.post(
            f"{BASE_URL}/projects/{project_id}/stage",
            json={"trusts": trust_ids},
            timeout=30,
        )
        assert stage_response.status_code < 300, (
            f"Failed to stage project: {stage_response.status_code} {stage_response.text}"
        )

    def test_approve_project(self, authed_client, cohort_query_sql, trust_ids, cleanup_projects):
        """A project can go through the full lifecycle to approval."""
        project_id, approval_data = create_and_approve_project(
            authed_client, cohort_query_sql, trust_ids, cleanup_projects,
            project_name="E2E Test - Approve Project",
        )

        assert approval_data.get("successful") is True, f"Project approval was not successful: {approval_data}"
        assert approval_data["trusts"]["processed"] > 0, "No trusts were processed"

        # Verify project status is persisted as APPROVED in the database
        project_response = authed_client.get(f"{BASE_URL}/projects/{project_id}", timeout=30)
        assert project_response.status_code < 300
        project_data = project_response.json()
        assert project_data["status"] == "APPROVED", f"Expected APPROVED status, got {project_data['status']}"
