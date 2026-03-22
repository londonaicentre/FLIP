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

import time
from typing import List, Tuple

import requests

from flip_api.domain.schemas.projects import ProjectDetails
from flip_api.utils.constants import BASE_URL
from tests.debug_prelaunch_task import (
    add_project_query,
    create_new_project,
    submit_query_to_trusts,
)
from tests.integration.utils import admin_authentication


def poll_until(fn, timeout_s=60, interval_s=5, description="condition"):
    """
    Poll fn() until it returns a truthy value, with timeout.

    Args:
        fn: Callable that returns a truthy value when the condition is met.
        timeout_s: Maximum time to wait in seconds.
        interval_s: Time between polls in seconds.
        description: Human-readable description for error messages.

    Returns:
        The truthy return value from fn().

    Raises:
        TimeoutError: If the condition is not met within the timeout.
    """
    deadline = time.time() + timeout_s
    last_exception = None

    while time.time() < deadline:
        try:
            result = fn()
            if result:
                return result
        except Exception as e:
            last_exception = e

        time.sleep(interval_s)

    msg = f"Timed out after {timeout_s}s waiting for: {description}"
    if last_exception:
        msg += f" (last error: {last_exception})"
    raise TimeoutError(msg)


def wait_for_service(url, timeout_s=30, interval_s=2):
    """
    Wait for an HTTP endpoint to return a 2xx status code.

    Args:
        url: The URL to check.
        timeout_s: Maximum time to wait in seconds.
        interval_s: Time between checks in seconds.

    Raises:
        TimeoutError: If the service does not respond within the timeout.
    """

    def check():
        try:
            resp = requests.get(url, timeout=5)
            return resp.status_code < 300
        except requests.ConnectionError:
            return False
        except requests.Timeout:
            return False

    poll_until(check, timeout_s=timeout_s, interval_s=interval_s, description=f"service at {url}")


def create_and_approve_project(
    authed_client: requests.Session,
    cohort_query_sql: str,
    trust_ids: List[str],
    cleanup_projects: List[str],
    project_name: str = "E2E Test Project",
) -> Tuple[str, dict]:
    """
    Create a project, add a cohort query, stage it, and approve it.

    Returns:
        Tuple of (project_id, approval_response_data).
    """
    project_data = ProjectDetails(
        name=project_name,
        description="Automated E2E test project",
        users=[],
    )
    response = create_new_project(authed_client, project_data)
    assert response is not None, "Failed to create project"
    assert response.status_code < 300, f"Create project failed: {response.status_code} {response.text}"
    project_id = response.json()["id"]
    cleanup_projects.append(project_id)

    # Add cohort query
    auth_token = admin_authentication()
    query_info = {
        "query": cohort_query_sql,
        "name": f"{project_name} Query",
        "project_id": str(project_id),
    }
    query_response = add_project_query(authed_client, query_info)
    assert query_response is not None, "Failed to save cohort query"
    assert query_response.status_code < 300, f"Save query failed: {query_response.status_code}"
    query_id = query_response.json()["query_id"]

    # Submit query to trusts
    submit_info = {
        "authenticationToken": auth_token.get("authorization", ""),
        "query": cohort_query_sql,
        "name": f"{project_name} Query",
        "project_id": str(project_id),
        "query_id": str(query_id),
    }
    submit_query_to_trusts(authed_client, submit_info)

    # Stage
    stage_response = authed_client.post(
        f"{BASE_URL}/projects/{project_id}/stage",
        json={"trusts": trust_ids},
        timeout=30,
    )
    assert stage_response.status_code < 300, f"Stage failed: {stage_response.status_code} {stage_response.text}"

    # Approve (triggers XNAT project creation + image retrieval)
    approve_response = authed_client.post(
        f"{BASE_URL}/step/project/{project_id}/approve",
        json={"trusts": trust_ids},
        timeout=60,
    )
    assert approve_response.status_code < 300, f"Approve failed: {approve_response.status_code} {approve_response.text}"

    return project_id, approve_response.json()
