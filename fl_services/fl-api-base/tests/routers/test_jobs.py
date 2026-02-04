# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

from unittest.mock import MagicMock

import pytest
from fastapi import status
from nvflare.fuel.flare_api.api_spec import JobNotFound

from fl_api.app import app
from fl_api.core.dependencies import get_session


@pytest.fixture(autouse=True)
def override_session(client):
    """Override get_session with a conditional MagicMock that matches NVFLARE behavior."""
    fake_session = MagicMock()
    existing_jobs = {"1234": "some_job"}

    # --- Job methods ---
    def abort_side_effect(job_id: str):
        if job_id not in existing_jobs:
            raise JobNotFound(f"Job {job_id} not found.")
        return {"status": "success", "info": f"Job {job_id} aborted."}

    def delete_side_effect(job_id: str):
        if job_id not in existing_jobs:
            raise JobNotFound(f"Job {job_id} not found.")
        return {"status": "success", "info": f"Job {job_id} deleted."}

    def reset_errors_side_effect(job_id: str):
        if job_id not in existing_jobs:
            raise JobNotFound(f"Job {job_id} not found.")
        return None

    def list_jobs_side_effect(detailed=False, limit=None, id_prefix=None, name_prefix=None, reverse=False):
        # mimic realistic output
        jobs = [
            {"id": "1234", "name": "training_round_1", "status": "completed"},
            {"id": "5678", "name": "training_round_2", "status": "running"},
        ]
        if limit:
            jobs = jobs[:limit]
        return jobs

    fake_session.abort_job.side_effect = abort_side_effect
    fake_session.delete_job.side_effect = delete_side_effect
    fake_session.reset_errors.side_effect = reset_errors_side_effect
    fake_session.list_jobs.side_effect = list_jobs_side_effect

    app.dependency_overrides[get_session] = lambda: fake_session
    yield fake_session  # ✅ yield it so tests can access it
    app.dependency_overrides.clear()


def test_list_jobs_success(client):
    response = client.get("/list_jobs")
    assert response.status_code == status.HTTP_200_OK

    jobs = response.json()
    assert isinstance(jobs, list)
    assert len(jobs) == 2
    assert all("id" in job for job in jobs)
    assert all("status" in job for job in jobs)


def test_list_jobs_with_limit(client):
    response = client.get("/list_jobs", params={"limit": 1})
    assert response.status_code == status.HTTP_200_OK

    jobs = response.json()
    assert len(jobs) == 1
    assert jobs[0]["id"] == "1234"


def test_list_jobs_internal_error(client, override_session):
    # Simulate an unexpected failure
    override_session.list_jobs.side_effect = Exception("DB connection lost")

    response = client.get("/list_jobs")
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


def test_reset_errors_success(client):
    response = client.post("/reset_errors", params={"job_id": "1234"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "success"
    assert "reset" in data["info"].lower()


def test_reset_errors_not_found(client):
    response = client.post("/reset_errors", params={"job_id": "9999"})
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_abort_job_success(client):
    response = client.delete("/abort_job/1234")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "success"
    assert "aborted" in data["info"]


def test_abort_job_not_found(client):
    response = client.delete("/abort_job/9999")
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_delete_job_success(client):
    response = client.delete("/delete_job/1234")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "success"
    assert "deleted" in data["info"]


def test_delete_job_not_found(client):
    response = client.delete("/delete_job/9999")
    assert response.status_code == status.HTTP_404_NOT_FOUND
