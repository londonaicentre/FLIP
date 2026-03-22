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

from typing import List

import pytest
import requests
from sqlmodel import Session

from flip_api.db.database import engine
from flip_api.utils.constants import BASE_URL
from tests.debug_post_debug_task import delete_project_data_in_order
from tests.integration.utils import admin_authentication


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def admin_token() -> dict:
    """Get admin authentication token via AWS Cognito."""
    return admin_authentication()


@pytest.fixture(scope="session")
def authed_client(admin_token) -> requests.Session:
    """Requests session pre-configured with admin auth headers."""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": admin_token.get("authorization", ""),
    })
    return session


@pytest.fixture(scope="session")
def trust_ids(authed_client, base_url) -> List[str]:
    """Fetch available trust IDs from the platform."""
    response = authed_client.get(f"{base_url}/trust/", timeout=30)
    assert response.status_code < 300, f"Failed to fetch trusts: {response.status_code} {response.text}"
    trusts = response.json()
    assert len(trusts) > 0, "No trusts found - is the platform seeded?"
    return [trust["id"] for trust in trusts]


@pytest.fixture(scope="session")
def cohort_query_sql() -> str:
    """Load the example cohort query SQL."""
    with open("tests/example_query.sql", "r") as f:
        return f.read()


@pytest.fixture
def cleanup_projects():
    """
    Collects project IDs during a test and deletes them in teardown.

    Usage:
        def test_something(cleanup_projects):
            project_id = create_project(...)
            cleanup_projects.append(project_id)
    """
    project_ids: List[str] = []
    yield project_ids

    if not project_ids:
        return

    try:
        with Session(engine) as session:
            success = delete_project_data_in_order(session, project_ids)
            if success:
                session.commit()
            else:
                session.rollback()
    except Exception as e:
        print(f"Warning: cleanup failed: {e}")
