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
E2E tests for cohort query round-trip.
Tests the full chain: Central Hub -> Trust API -> Data Access API -> OMOP DB -> Central Hub.
"""

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


@pytest.mark.e2e
class TestCohortQueryRoundTrip:
    """Test that cohort queries execute end-to-end and return results from trusts."""

    def test_cohort_query_returns_results(self, authed_client, cohort_query_sql, cleanup_projects):
        """
        Submit a cohort query and verify results come back from the OMOP database.

        This tests the complete data flow:
        1. Central Hub saves the query
        2. Central Hub submits to trust APIs
        3. Trust API forwards to Data Access API
        4. Data Access API executes against OMOP DB
        5. Results are posted back to Central Hub
        6. Central Hub stores and returns aggregated results
        """
        # Create project
        project_data = ProjectDetails(
            name="E2E Test - Cohort Query Round-Trip",
            description="Tests full cohort query execution across services",
            users=[],
        )
        response = create_new_project(authed_client, project_data)
        assert response is not None, "Failed to create project"
        project_id = response.json()["id"]
        cleanup_projects.append(project_id)

        # Save cohort query
        query_info = {
            "query": cohort_query_sql,
            "name": "E2E Cohort Round-Trip Query",
            "project_id": str(project_id),
        }
        query_response = add_project_query(authed_client, query_info)
        assert query_response is not None, "Failed to save cohort query"
        query_id = query_response.json()["query_id"]

        # Submit query to trusts
        auth_token = admin_authentication()
        submit_info = {
            "authenticationToken": auth_token.get("authorization", ""),
            "query": cohort_query_sql,
            "name": "E2E Cohort Round-Trip Query",
            "project_id": str(project_id),
            "query_id": str(query_id),
        }
        submit_response = submit_query_to_trusts(authed_client, submit_info)
        assert submit_response is not None, "Failed to submit query to trusts"

        # Poll for results - trusts process asynchronously and post results back
        def check_results():
            resp = authed_client.get(f"{BASE_URL}/cohort/{query_id}", timeout=30)
            if resp.status_code == 404:
                return None  # Results not yet available
            if resp.status_code < 300:
                data = resp.json()
                # Check that we got meaningful results back
                if data and data.get("data"):
                    return data
            return None

        results = poll_until(
            check_results,
            timeout_s=90,
            interval_s=5,
            description="cohort query results from OMOP database",
        )

        assert results is not None, "No cohort query results returned"
        assert "data" in results, "Results should contain 'data' field"
        assert len(results["data"]) > 0, "Results data should not be empty"
