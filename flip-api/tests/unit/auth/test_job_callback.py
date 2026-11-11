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

"""Which callbacks about a model the hub accepts from the FL server."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from flip_api.auth.job_callback import authorise_job_callback, reporting_jobs
from flip_api.db.models.main_models import FLJob, Model
from flip_api.domain.schemas.status import JobStatus, ModelStatus


def _session(job_statuses: list[JobStatus], model_status: ModelStatus) -> tuple[MagicMock, list[FLJob]]:
    """A session whose model has these non-deleted jobs, newest first."""
    model_id = uuid4()
    jobs = [FLJob(model_id=model_id, status=job_status) for job_status in job_statuses]
    session = MagicMock()
    session.exec.return_value.all.return_value = jobs
    session.get.return_value = MagicMock(spec=Model, status=model_status)
    return session, jobs


@pytest.mark.parametrize(
    ("job_statuses", "model_status", "reporting"),
    [
        # A running job reports, whatever the model's status: its own steps, a stop that has not reached it yet.
        ([JobStatus.IN_PROGRESS], ModelStatus.INITIATED, [0]),
        ([JobStatus.IN_PROGRESS], ModelStatus.RUNNING, [0]),
        ([JobStatus.IN_PROGRESS], ModelStatus.ERROR, [0]),
        # A model started twice: the job that is running reports, the queued one does not.
        ([JobStatus.QUEUED, JobStatus.IN_PROGRESS], ModelStatus.RUNNING, [1]),
        # Flower trains on after a client error has completed the job, and still has results to report.
        ([JobStatus.COMPLETED], ModelStatus.ERROR, [0]),
        ([JobStatus.COMPLETED, JobStatus.COMPLETED], ModelStatus.ERROR, [0]),
        # Once the run has reported its results, or was stopped, it has nothing left to say.
        ([JobStatus.COMPLETED], ModelStatus.RESULTS_UPLOADED, []),
        ([JobStatus.COMPLETED], ModelStatus.RESULTS_UPLOAD_FAILED, []),
        ([JobStatus.COMPLETED], ModelStatus.STOPPED, []),
        # A retry is waiting: the earlier, failed run must not report into it.
        ([JobStatus.QUEUED, JobStatus.COMPLETED], ModelStatus.ERROR, []),
        ([JobStatus.QUEUED], ModelStatus.INITIATED, []),
        # No job, or only deleted ones (a user's stop, a failed start), or no such model.
        ([], ModelStatus.STOPPED, []),
        ([], ModelStatus.ERROR, []),
        ([], ModelStatus.PENDING, []),
    ],
)
def test_reporting_jobs(job_statuses: list[JobStatus], model_status: ModelStatus, reporting: list[int]) -> None:
    session, jobs = _session(job_statuses, model_status)

    assert reporting_jobs(uuid4(), session) == [jobs[index] for index in reporting]


def test_callback_is_accepted_for_a_reporting_job() -> None:
    with patch("flip_api.auth.job_callback.reporting_jobs", return_value=[MagicMock()]):
        authorise_job_callback(uuid4(), MagicMock(), None)


def test_callback_is_refused_without_a_reporting_job() -> None:
    with patch("flip_api.auth.job_callback.reporting_jobs", return_value=[]):
        with pytest.raises(HTTPException) as refused:
            authorise_job_callback(uuid4(), MagicMock(), None)

    assert refused.value.status_code == 403
    assert refused.value.detail == "Not authorised for this model."
