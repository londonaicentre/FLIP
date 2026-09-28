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
from sqlmodel import Session, create_engine, select

from flip_api.db.models.main_models import FLJob, FLLogs, FLNets, FLScheduler
from flip_api.domain.interfaces.fl import IJobMetaData
from flip_api.domain.schemas.status import FLJobStatus, JobStatus, ModelStatus, NetStatus, ProjectStatus
from flip_api.domain.schemas.types import FLBackend
from flip_api.fl_services.reconcile_failed_jobs import reconcile_failed_fl_jobs
from flip_api.fl_services.services.fl_service import RunLogTail

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
            "flip_api.fl_services.reconcile_failed_jobs.get_backend_job_metadata",
            return_value=IJobMetaData(job_id=_BACKEND_JOB_ID, status=FLJobStatus.FAILED),
        ) as mock_status,
        patch(
            "flip_api.fl_services.reconcile_failed_jobs.fetch_run_logs",
            return_value=RunLogTail(log="ERROR: ServerApp raised an exception\nImportError: cannot import name 'x'"),
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

    with patch("flip_api.fl_services.reconcile_failed_jobs.get_backend_job_metadata") as mock_status:
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
        patch("flip_api.fl_services.reconcile_failed_jobs.get_backend_job_metadata", return_value=None),
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


_MODULE = "flip_api.fl_services.reconcile_failed_jobs"


def _failure_rows(session, model_id):
    return [
        row for row in session.exec(select(FLLogs).where(FLLogs.model_id == model_id)).all() if row.success is False
    ]


def _state(session, ctx):
    session.expire_all()
    job = session.get(FLJob, ctx["job"].id)
    scheduler = session.get(FLScheduler, ctx["scheduler"].id)
    assert job is not None
    assert scheduler is not None
    return ctx["model"].status, job.status, scheduler.status


def test_unsubmitted_job_is_not_polled(session, submitted_in_flight_job):
    """`started` lands at pickup; the backend id only after bundle upload + submit.

    A job in that window has nothing to ask the backend about -- and polled with id None it
    would come back "unlisted" and, past the grace, be errored as a backend restart.
    """
    ctx = submitted_in_flight_job
    ctx["job"].fl_backend_job_id = None
    ctx["job"].started = datetime.utcnow() - timedelta(hours=2)
    session.add(ctx["job"])
    session.commit()

    with patch(f"{_MODULE}.get_backend_job_metadata") as mock_status:
        reported = reconcile_failed_fl_jobs(session)

    assert reported == 0
    mock_status.assert_not_called()
    assert _state(session, ctx) == (ModelStatus.INITIATED, JobStatus.IN_PROGRESS, NetStatus.BUSY)


def test_leaked_net_of_a_settled_model_is_released_without_polling(session, submitted_in_flight_job):
    """Model already ERROR, job still IN_PROGRESS, net still BUSY: the release commit was lost.

    Nothing else revisits this state (run_jobs' stale-BUSY recovery skips IN_PROGRESS jobs),
    so the sweep must select it even though the model is no longer in flight.
    """
    ctx = submitted_in_flight_job
    ctx["model"].status = ModelStatus.ERROR
    session.add(ctx["model"])
    session.commit()

    with patch(f"{_MODULE}.get_backend_job_metadata") as mock_status:
        reported = reconcile_failed_fl_jobs(session)

    assert reported == 0
    mock_status.assert_not_called()
    assert _state(session, ctx) == (ModelStatus.ERROR, JobStatus.COMPLETED, NetStatus.AVAILABLE)
    assert _failure_rows(session, ctx["model"].id) == []


def test_dead_stale_job_of_a_retried_model_frees_only_its_own_net(session, submitted_in_flight_job):
    """The model was re-initiated over the in-flight job, so a newer job exists.

    The older job's run is dead: its net comes back, but the model -- whose newest job is the
    healthy retry -- is untouched, and so is the retry's own job and net.
    """
    ctx = submitted_in_flight_job
    retry_net = FLNets(name=f"net-{uuid4()}", endpoint=f"http://fl-api-{uuid4()}:5000", fl_backend=FLBackend.FLOWER)
    session.add(retry_net)
    session.flush()
    retry_job = FLJob(
        model_id=ctx["model"].id,
        status=JobStatus.IN_PROGRESS,
        created=datetime.utcnow() + timedelta(seconds=1),
        started=datetime.utcnow(),
        fl_backend_job_id="retry-run",
    )
    session.add(retry_job)
    session.flush()
    retry_scheduler = FLScheduler(net_id=retry_net.id, status=NetStatus.BUSY, job_id=retry_job.id)
    session.add(retry_scheduler)
    session.commit()

    def status_for(_endpoint, backend_job_id):
        if backend_job_id == _BACKEND_JOB_ID:
            return IJobMetaData(job_id=_BACKEND_JOB_ID, status=FLJobStatus.FAILED)
        return IJobMetaData(job_id="retry-run", status=FLJobStatus.RUNNING)

    with (
        patch(f"{_MODULE}.get_backend_job_metadata", side_effect=status_for),
        patch(f"{_MODULE}.fetch_run_logs", return_value=None),
    ):
        reported = reconcile_failed_fl_jobs(session)

    assert reported == 0
    assert _state(session, ctx) == (ModelStatus.INITIATED, JobStatus.COMPLETED, NetStatus.AVAILABLE)
    retry = session.get(FLJob, retry_job.id)
    retry_sched = session.get(FLScheduler, retry_scheduler.id)
    assert retry is not None
    assert retry.status == JobStatus.IN_PROGRESS
    assert retry_sched is not None
    assert retry_sched.status == NetStatus.BUSY
    assert _failure_rows(session, ctx["model"].id) == []


def test_a_failed_status_write_leaves_no_orphan_feed_row(session, submitted_in_flight_job):
    """The feed row and the ERROR are one commit: if the status write blows up, neither lands."""
    ctx = submitted_in_flight_job

    with (
        patch(
            f"{_MODULE}.get_backend_job_metadata",
            return_value=IJobMetaData(job_id=_BACKEND_JOB_ID, status=FLJobStatus.FAILED),
        ),
        patch(f"{_MODULE}.fetch_run_logs", return_value=RunLogTail(log="ImportError: boom")),
        patch(f"{_MODULE}.update_model_status", side_effect=RuntimeError("db connection dropped")),
    ):
        reported = reconcile_failed_fl_jobs(session)

    assert reported == 0
    # Through a session that never saw the sweep's pending state.
    with Session(session.get_bind()) as fresh:
        model = fresh.get(type(ctx["model"]), ctx["model"].id)
        assert model is not None
        assert model.status == ModelStatus.INITIATED
        assert _failure_rows(fresh, ctx["model"].id) == []


def test_run_stopped_outside_flip_is_resolved_end_to_end(session, submitted_in_flight_job):
    ctx = submitted_in_flight_job

    with (
        patch(
            f"{_MODULE}.get_backend_job_metadata",
            return_value=IJobMetaData(job_id=_BACKEND_JOB_ID, status=FLJobStatus.STOPPED),
        ),
        patch(f"{_MODULE}.fetch_run_logs") as mock_logs,
    ):
        reported = reconcile_failed_fl_jobs(session)

    assert reported == 1
    mock_logs.assert_not_called()
    assert _state(session, ctx) == (ModelStatus.ERROR, JobStatus.COMPLETED, NetStatus.AVAILABLE)
    rows = _failure_rows(session, ctx["model"].id)
    assert len(rows) == 1
    assert "stopped outside FLIP" in (rows[0].log or "")


def _other_connection(session):
    """A session on its own connection: the test engine is a StaticPool, so a second Session on
    it would share the sweep's connection (and commit the sweep's transaction)."""
    return Session(create_engine(session.get_bind().url.render_as_string(hide_password=False)))


def test_hubs_own_abort_landing_mid_poll_wins(session, submitted_in_flight_job):
    """The abort path dequeues the job, then stops the run, then settles the model.

    Seen from the sweep: the job was IN_PROGRESS at selection, the backend answers STOPPED,
    and the job re-reads as DELETED. The abort owns the outcome -- no ERROR, no feed row.
    """
    ctx = submitted_in_flight_job

    def abort_dequeues_then_stops(_endpoint, _backend_job_id):
        with _other_connection(session) as other:
            job = other.get(FLJob, ctx["job"].id)
            assert job is not None
            job.status = JobStatus.DELETED
            other.add(job)
            other.commit()
        return IJobMetaData(job_id=_BACKEND_JOB_ID, status=FLJobStatus.STOPPED)

    with patch(f"{_MODULE}.get_backend_job_metadata", side_effect=abort_dequeues_then_stops):
        reported = reconcile_failed_fl_jobs(session)

    assert reported == 0
    assert _state(session, ctx) == (ModelStatus.INITIATED, JobStatus.DELETED, NetStatus.BUSY)
    assert _failure_rows(session, ctx["model"].id) == []


def test_result_landing_mid_poll_wins(session, submitted_in_flight_job):
    """A RESULTS_UPLOADED committed elsewhere during the status call is seen by the re-read."""
    ctx = submitted_in_flight_job

    def result_lands_during_the_poll(_endpoint, _backend_job_id):
        with _other_connection(session) as other:
            model = other.get(type(ctx["model"]), ctx["model"].id)
            assert model is not None
            model.status = ModelStatus.RESULTS_UPLOADED
            other.add(model)
            other.commit()
        return IJobMetaData(job_id=_BACKEND_JOB_ID, status=FLJobStatus.FAILED)

    with (
        patch(f"{_MODULE}.get_backend_job_metadata", side_effect=result_lands_during_the_poll),
        patch(f"{_MODULE}.fetch_run_logs", return_value=None),
    ):
        reported = reconcile_failed_fl_jobs(session)

    assert reported == 0
    model_status, _, _ = _state(session, ctx)
    assert model_status == ModelStatus.RESULTS_UPLOADED
    assert _failure_rows(session, ctx["model"].id) == []
