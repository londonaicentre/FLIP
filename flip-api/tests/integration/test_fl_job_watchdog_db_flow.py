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

"""Integration coverage of the FL job watchdog (#727) against the throwaway Postgres.

The FLJob -> FLScheduler -> FLNets -> Model join and the model-row lock are SQL-shaped, so mocked sessions would
pass them silently.
"""

from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlmodel import select

from flip_api.db.models.main_models import FLJob, FLLogs, FLNets, FLScheduler, Model
from flip_api.domain.interfaces.fl import IJobMetaData
from flip_api.domain.schemas.status import FLJobStatus, JobStatus, ModelStatus, NetStatus, ProjectStatus
from flip_api.domain.schemas.types import FLBackend
from flip_api.fl_services.watch_jobs import JobWatchdog, WatchedJob, _fail_job, find_watched_jobs

PREPARE_TIMEOUT = timedelta(minutes=30)


@pytest.fixture
def running_job(session, user_factory, project_factory, model_factory):
    """A model RUNNING on net BUSY with job IN_PROGRESS, submitted to the backend as ``run-1``."""
    user = user_factory()
    project = project_factory.build(owner_id=user.id, status=ProjectStatus.APPROVED, deleted=False)
    session.add(project)
    session.flush()

    model = model_factory.build(project_id=project.id, owner_id=user.id, status=ModelStatus.RUNNING, deleted=False)
    session.add(model)
    session.flush()

    net = FLNets(name=f"net-{uuid4()}", endpoint=f"http://fl-api-{uuid4()}:8000", fl_backend=FLBackend.NVFLARE)
    session.add(net)
    session.flush()

    job = FLJob(
        model_id=model.id,
        status=JobStatus.IN_PROGRESS,
        started=datetime.utcnow() - timedelta(hours=1),
        fl_backend_job_id="run-1",
    )
    session.add(job)
    session.flush()

    scheduler = FLScheduler(net_id=net.id, status=NetStatus.BUSY, job_id=job.id)
    session.add(scheduler)
    session.commit()

    return {"model": model, "job": job, "scheduler": scheduler, "net": net, "user": user, "project": project}


def test_find_watched_jobs_returns_the_running_job_with_its_net(session, running_job):
    watched = find_watched_jobs(session)

    assert [(w.job_id, w.model_id, w.fl_backend_job_id, w.net_name, w.net_endpoint) for w in watched] == [
        (
            running_job["job"].id,
            running_job["model"].id,
            "run-1",
            running_job["net"].name,
            running_job["net"].endpoint,
        )
    ]


@pytest.mark.parametrize("model_status", [ModelStatus.RESULTS_UPLOADED, ModelStatus.ERROR, ModelStatus.STOPPED])
def test_find_watched_jobs_skips_a_model_that_has_ended(session, running_job, model_status):
    running_job["model"].status = model_status
    session.commit()

    assert find_watched_jobs(session) == []


def test_find_watched_jobs_skips_an_older_deleted_job_of_a_requeued_model(session, running_job):
    # Review Focus 4: a stopped-then-requeued model keeps its earlier DELETED job; only the current one counts.
    old = FLJob(model_id=running_job["model"].id, status=JobStatus.DELETED, fl_backend_job_id="run-0")
    session.add(old)
    session.flush()
    session.add(FLScheduler(net_id=running_job["net"].id, status=NetStatus.BUSY, job_id=old.id))
    session.commit()

    assert [w.fl_backend_job_id for w in find_watched_jobs(session)] == ["run-1"]


def test_find_watched_jobs_skips_a_released_net(session, running_job):
    running_job["scheduler"].status = NetStatus.AVAILABLE
    session.commit()

    assert find_watched_jobs(session) == []


def _backend_says(status: FLJobStatus):
    return patch(
        "flip_api.fl_services.watch_jobs.list_backend_jobs",
        return_value=[IJobMetaData(job_id="run-1", status=status)],
    )


def test_a_failed_backend_job_errors_the_model_and_frees_the_net(session, running_job):
    with _backend_says(FLJobStatus.FAILED):
        failed = JobWatchdog(strikes=1, prepare_timeout=PREPARE_TIMEOUT).tick(session, datetime.utcnow())

    session.expire_all()
    model = session.get(Model, running_job["model"].id)
    job = session.get(FLJob, running_job["job"].id)
    scheduler = session.get(FLScheduler, running_job["scheduler"].id)
    assert failed == [running_job["model"].id]
    assert model is not None
    assert model.status == ModelStatus.ERROR
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert scheduler is not None
    assert scheduler.status == NetStatus.AVAILABLE
    logs = session.exec(select(FLLogs).where(FLLogs.model_id == running_job["model"].id)).all()
    assert [(log.log, log.success) for log in logs] == [
        (f"The FL server on {running_job['net'].name} reports this job failed before it reported a result.", False)
    ]


def test_a_job_never_submitted_errors_the_model_after_the_timeout(session, running_job):
    running_job["job"].fl_backend_job_id = None
    running_job["job"].started = datetime.utcnow() - timedelta(hours=2)
    session.commit()

    with patch("flip_api.fl_services.watch_jobs.list_backend_jobs") as list_jobs:
        failed = JobWatchdog(strikes=1, prepare_timeout=PREPARE_TIMEOUT).tick(session, datetime.utcnow())

    session.expire_all()
    scheduler = session.get(FLScheduler, running_job["scheduler"].id)
    list_jobs.assert_not_called()
    assert failed == [running_job["model"].id]
    assert scheduler is not None
    assert scheduler.status == NetStatus.AVAILABLE


@pytest.mark.parametrize("arrived", [ModelStatus.RESULTS_UPLOADED, ModelStatus.STOPPED])
def test_a_status_that_arrives_first_is_never_overwritten(session, running_job, arrived):
    # Review Focus 2 and 5: the model's own result, or a user's stop, lands after the watchdog read the job list
    # and before it writes. _fail_job re-reads the model under a row lock and leaves it alone.
    job = WatchedJob(
        job_id=running_job["job"].id,
        model_id=running_job["model"].id,
        fl_backend_job_id="run-1",
        started=running_job["job"].started,
        net_name=running_job["net"].name,
        net_endpoint=running_job["net"].endpoint,
    )
    running_job["model"].status = arrived
    session.commit()

    assert _fail_job(job, "reason", session) is False

    session.expire_all()
    model = session.get(Model, running_job["model"].id)
    assert model is not None
    assert model.status == arrived
    assert session.exec(select(FLLogs).where(FLLogs.model_id == running_job["model"].id)).all() == []


def test_the_watched_jobs_net_is_freed_even_when_the_model_has_a_newer_job(session, running_job):
    # A second initiate (a double POST) leaves a newer QUEUED job beside the running one. update_model_status(ERROR)
    # completes the newest job, so the watchdog must free the watched job's net itself, or the net stays BUSY with
    # its model already ERROR and nothing left to notice it.
    newer = FLJob(
        model_id=running_job["model"].id,
        status=JobStatus.QUEUED,
        created=datetime.utcnow() + timedelta(seconds=1),
    )
    session.add(newer)
    session.commit()

    with _backend_says(FLJobStatus.FAILED):
        JobWatchdog(strikes=1, prepare_timeout=PREPARE_TIMEOUT).tick(session, datetime.utcnow())

    session.expire_all()
    scheduler = session.get(FLScheduler, running_job["scheduler"].id)
    assert scheduler is not None
    assert scheduler.status == NetStatus.AVAILABLE
