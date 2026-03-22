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

from flip_api.utils.constants import BASE_URL
from tests.e2e.helpers import create_and_submit_project, poll_until


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
        _, query_id = create_and_submit_project(
            authed_client, cohort_query_sql, cleanup_projects,
            project_name="E2E Test - Cohort Query Round-Trip",
        )

        def check_results():
            resp = authed_client.get(f"{BASE_URL}/cohort/{query_id}", timeout=30)
            if resp.status_code == 404:
                return None  # Results not yet available
            if resp.status_code < 300:
                data = resp.json()
                if data and "data" in data and data["data"] is not None:
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
