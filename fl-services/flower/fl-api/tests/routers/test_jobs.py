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

from fl_api.app import app
from fl_api.core.dependencies import get_session
from fl_api.utils.exception_handlers import JobNotFound
from fl_api.utils.schemas import JobMetaData


@pytest.fixture(autouse=True)
def override_session(client):
    """Override get_session with a conditional MagicMock that matches Flower behavior."""
    fake_session = MagicMock()
    existing_jobs = {"1234": "some_job"}

    # --- Job methods ---
    def abort_side_effect(job_id: str):
        if job_id not in existing_jobs:
            raise JobNotFound(f"Job {job_id} not found.")
        return {"status": "success", "info": f"Job {job_id} aborted."}

    def list_jobs_side_effect():
        # mimic realistic output
        jobs = [
            JobMetaData(job_id="1234", job_name="training_round_1", status="completed"),
            JobMetaData(job_id="5678", job_name="training_round_2", status="running"),
        ]
        return jobs

    fake_session.abort_job.side_effect = abort_side_effect
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
    assert all("job_id" in job for job in jobs)
    assert all("status" in job for job in jobs)


def test_abort_job_success(client):
    response = client.delete("/abort_job/1234")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "success"
    assert "aborted" in data["info"]


def test_abort_job_not_found(client):
    response = client.delete("/abort_job/9999")
    assert response.status_code == status.HTTP_404_NOT_FOUND
