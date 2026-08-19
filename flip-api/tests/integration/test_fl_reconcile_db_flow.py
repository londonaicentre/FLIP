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

"""Integration coverage of the failed-FL-job reconcile (#1001) against the throwaway Postgres.

Two things here are SQL-shaped and silently passable under mocked sessions: the
FLJob→FLScheduler→FLNets→Model join that selects the in-flight jobs (a wrong join returns
zero rows and the sweep becomes a no-op nobody notices), and the cross-table resolution the
sweep delegates to ``update_model_status(ERROR)`` — the FLJob completing and the scheduler
returning to AVAILABLE.
"""

from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlmodel import select

from flip_api.db.models.main_models import FLJob, FLLogs, FLNets, FLScheduler
from flip_api.domain.schemas.status import FLJobStatus, JobStatus, ModelStatus, NetStatus, ProjectStatus
from flip_api.domain.schemas.types import FLBackend
from flip_api.fl_services.reconcile_failed_jobs import reconcile_failed_fl_jobs

_BACKEND_JOB_ID = "11536428743664681318"


@pytest.fixture
def submitted_in_flight_job(session, user_factory, project_factory, model_factory):
    """A submitted, in-flight job: model INITIATED, job IN_PROGRESS with a backend id, net BUSY.

    Mirrors the state after ``submit_job`` returned and before the run reports anything —
    the window the reconcile exists for.
    """
    user = user_factory()
    project = project_factory.build(owner_id=user.id, status=ProjectStatus.APPROVED, deleted=False)
    session.add(project)
    session.flush()

    model = model_factory.build(
        project_id=project.id,
        owner_id=user.id,
        status=ModelStatus.INITIATED,
        deleted=False,
    )
    session.add(model)
    session.flush()

    net = FLNets(name=f"net-{uuid4()}", endpoint=f"http://fl-api-{uuid4()}:5000", fl_backend=FLBackend.FLOWER)
    session.add(net)
    session.flush()

    job = FLJob(
        model_id=model.id,
        status=JobStatus.IN_PROGRESS,
        started=datetime.utcnow() - timedelta(minutes=5),
        fl_backend_job_id=_BACKEND_JOB_ID,
    )
    session.add(job)
    session.flush()

    scheduler = FLScheduler(net_id=net.id, status=NetStatus.BUSY, job_id=job.id)
    session.add(scheduler)
    session.commit()

    return {"model": model, "job": job, "scheduler": scheduler, "net": net}


def test_failed_run_is_resolved_end_to_end(session, submitted_in_flight_job):
    """A FAILED run errors the model, records the log tail, completes the job and frees the net."""
    ctx = submitted_in_flight_job

    with (
        patch(
            "flip_api.fl_services.reconcile_failed_jobs.get_backend_job_status",
            return_value=FLJobStatus.FAILED,
        ) as mock_status,
        patch(
            "flip_api.fl_services.reconcile_failed_jobs.fetch_run_logs",
            return_value="ERROR: ServerApp raised an exception\nImportError: cannot import name 'x'",
        ),
    ):
        reported = reconcile_failed_fl_jobs(session)

    assert reported == 1
    mock_status.assert_called_once_with(ctx["net"].endpoint, _BACKEND_JOB_ID)

    session.expire_all()
    assert ctx["model"].status == ModelStatus.ERROR
    job = session.get(FLJob, ctx["job"].id)
    scheduler = session.get(FLScheduler, ctx["scheduler"].id)
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert scheduler is not None
    assert scheduler.status == NetStatus.AVAILABLE

    logs = session.exec(select(FLLogs).where(FLLogs.model_id == ctx["model"].id)).all()
    failure_rows = [row for row in logs if row.success is False]
    assert len(failure_rows) == 1
    assert _BACKEND_JOB_ID in (failure_rows[0].log or "")
    assert "ImportError" in (failure_rows[0].log or "")


def test_settled_job_is_not_even_polled(session, submitted_in_flight_job):
    """A COMPLETED job (or settled model) never reaches the FL API — the join filters it out."""
    ctx = submitted_in_flight_job
    ctx["job"].status = JobStatus.COMPLETED
    session.add(ctx["job"])
    session.commit()

    with patch("flip_api.fl_services.reconcile_failed_jobs.get_backend_job_status") as mock_status:
        reported = reconcile_failed_fl_jobs(session)

    assert reported == 0
    mock_status.assert_not_called()


def test_unlisted_run_past_grace_is_resolved_end_to_end(session, submitted_in_flight_job):
    """A run its backend forgot (SuperLink restart) is resolved once past the grace period."""
    ctx = submitted_in_flight_job
    ctx["job"].started = datetime.utcnow() - timedelta(hours=2)
    session.add(ctx["job"])
    session.commit()

    with (
        patch("flip_api.fl_services.reconcile_failed_jobs.get_backend_job_status", return_value=None),
        patch("flip_api.fl_services.reconcile_failed_jobs.fetch_run_logs") as mock_logs,
    ):
        reported = reconcile_failed_fl_jobs(session)

    assert reported == 1
    mock_logs.assert_not_called()

    session.expire_all()
    assert ctx["model"].status == ModelStatus.ERROR
    logs = session.exec(select(FLLogs).where(FLLogs.model_id == ctx["model"].id)).all()
    failure_rows = [row for row in logs if row.success is False]
    assert len(failure_rows) == 1
    assert "no longer listed" in (failure_rows[0].log or "")
