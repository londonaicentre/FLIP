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

"""Hub-side watchdog for FL jobs that end without reporting a result (#727).

A job's server-side process reports its own terminal status (RESULTS_UPLOADED, ERROR, ...) to the hub. If that
process dies first, nothing does: the model stays RUNNING and its net BUSY. This compares every job the hub believes
is running with its net's FL API and fails the ones the backend has ended or lost.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from uuid import UUID

from sqlmodel import Session, col, select

from flip_api.config import get_settings
from flip_api.db.database import get_engine
from flip_api.db.models.main_models import FLJob, FLNets, FLScheduler, Model
from flip_api.domain.interfaces.fl import IJobMetaData
from flip_api.domain.schemas.status import FLJobStatus, JobStatus, ModelStatus, NetStatus
from flip_api.fl_services.services.fl_scheduler_service import release_scheduler_for_model
from flip_api.fl_services.services.fl_service import list_backend_jobs
from flip_api.model_services.services.model_service import add_log, update_model_status
from flip_api.utils.logger import logger

_ALIVE = {FLJobStatus.PENDING, FLJobStatus.RUNNING}

# Worst first: if a backend ever lists one job id twice, the reason names the worse ending.
_ENDED = {
    FLJobStatus.FAILED: "The FL server on {net} reports this job failed before it reported a result.",
    FLJobStatus.STOPPED: "The FL server on {net} stopped this job before it reported a result.",
    FLJobStatus.FINISHED: "The FL server on {net} finished this job, but its result never reached FLIP.",
}


@dataclass(frozen=True)
class WatchedJob:
    """A job the hub believes is running, with the net it runs on."""

    job_id: UUID
    model_id: UUID
    fl_backend_job_id: str | None
    started: datetime | None
    net_name: str
    net_endpoint: str


# A model in one of these was picked up and has not reported a terminal status.
RUNNING_MODEL_STATUSES = (ModelStatus.INITIATED, ModelStatus.PREPARED, ModelStatus.RUNNING)


def find_watched_jobs(session: Session) -> list[WatchedJob]:
    """List the jobs the hub believes are running: picked up, holding a BUSY net, model not ended.

    Args:
        session (Session): The database session.

    Returns:
        list[WatchedJob]: One entry per running job.
    """
    statement = (
        select(FLJob, FLNets)
        .join(FLScheduler, col(FLScheduler.job_id) == FLJob.id)
        .join(FLNets, col(FLNets.id) == FLScheduler.net_id)
        .join(Model, col(Model.id) == FLJob.model_id)
        .where(
            FLJob.status == JobStatus.IN_PROGRESS,
            FLScheduler.status == NetStatus.BUSY,
            col(Model.status).in_(RUNNING_MODEL_STATUSES),
        )
    )
    return [
        WatchedJob(
            job_id=job.id,
            model_id=job.model_id,
            fl_backend_job_id=job.fl_backend_job_id,
            started=job.started,
            net_name=net.name,
            net_endpoint=net.endpoint,
        )
        for job, net in session.exec(statement).all()
    ]


def why_job_is_dead(
    job: WatchedJob,
    backend_jobs: list[IJobMetaData] | None,
    now: datetime,
    prepare_timeout: timedelta,
) -> str | None:
    """Say why a job the hub believes is running has in fact ended, or None if it has not.

    Args:
        job (WatchedJob): The job.
        backend_jobs (list[IJobMetaData] | None): Every job on the job's net, or None if its FL API could not say.
        now (datetime): The current UTC time, naive like ``FLJob.started``.
        prepare_timeout (timedelta): How long after pickup a job may still have no backend id.

    Returns:
        str | None: The log line to show on the model, or None while the job is alive or its state unknown.
    """
    if job.fl_backend_job_id is None:
        if job.started is not None and now - job.started > prepare_timeout:
            minutes = int(prepare_timeout.total_seconds() // 60)
            return (
                f"Training never reached the FL server on {job.net_name}: "
                f"it was still being prepared after {minutes} minutes."
            )
        return None

    if backend_jobs is None:
        return None

    statuses = {j.status for j in backend_jobs if j.job_id == job.fl_backend_job_id}
    if not statuses:
        return f"The FL server on {job.net_name} no longer has this job, for example because it restarted."
    if statuses & _ALIVE:
        return None
    for status, reason in _ENDED.items():
        if status in statuses:
            return reason.format(net=job.net_name)
    return None


def _fail_job(job: WatchedJob, reason: str, session: Session) -> bool:
    """Set a dead job's model to ERROR and log why, unless the model ended meanwhile.

    The model row is locked and re-read first. A terminal status that arrived after the job list was read (the
    model's own result, a user's stop) therefore stands, and two flip-api replicas cannot both fail the model and
    log twice. ``update_model_status(ERROR)`` marks the job COMPLETED and frees the net.

    Args:
        job (WatchedJob): The dead job.
        reason (str): The log line to show on the model.
        session (Session): The database session.

    Returns:
        bool: True if the model was failed, False if it had already ended.
    """
    model = session.exec(select(Model).where(Model.id == job.model_id).with_for_update()).first()
    if model is None or model.status not in RUNNING_MODEL_STATUSES:
        session.rollback()
        return False

    logger.error(
        {
            "message": "FL job ended without reporting a result; setting its model to ERROR",
            "model": job.model_id,
            "job": job.job_id,
            "net": job.net_name,
            "reason": reason,
        }
    )
    # Status first: its commit releases the lock only once the model is ERROR, so a second replica skips it.
    update_model_status(job.model_id, ModelStatus.ERROR, session)
    # update_model_status frees the net of the model's newest job. A model started twice has a newer QUEUED job
    # beside this one, so free this job's net explicitly too (a no-op when it is already free).
    release_scheduler_for_model(job.model_id, session)
    add_log(job.model_id, reason, session, success=False)
    return True


def _list_or_unknown(net_endpoint: str) -> list[IJobMetaData] | None:
    """List one net's jobs, or None for any error, so one broken net cannot stop the others being watched."""
    try:
        return list_backend_jobs(net_endpoint)
    except Exception:
        logger.exception(f"Could not list the jobs on {net_endpoint}")
        return None


class JobWatchdog:
    """Fail the model of any running job its backend has ended or lost, once it has looked dead ``strikes`` ticks in
    a row.

    Args:
        strikes (int): Consecutive ticks a job must look dead before its model is failed.
        prepare_timeout (timedelta): How long after pickup a job may still have no backend id.
    """

    def __init__(self, strikes: int, prepare_timeout: timedelta) -> None:
        self.strikes = strikes
        self.prepare_timeout = prepare_timeout
        self._looked_dead: dict[UUID, int] = {}

    def tick(self, session: Session, now: datetime) -> list[UUID]:
        """Check every running job once.

        Args:
            session (Session): The database session.
            now (datetime): The current UTC time, naive like ``FLJob.started``.

        Returns:
            list[UUID]: The ids of the models this tick set to ERROR.
        """
        jobs = find_watched_jobs(session)
        endpoints = {job.net_endpoint for job in jobs if job.fl_backend_job_id is not None}
        backend_jobs = {endpoint: _list_or_unknown(endpoint) for endpoint in endpoints}

        failed: list[UUID] = []
        looked_dead: dict[UUID, int] = {}
        for job in jobs:
            reason = why_job_is_dead(job, backend_jobs.get(job.net_endpoint), now, self.prepare_timeout)
            if reason is None:
                continue
            count = self._looked_dead.get(job.job_id, 0) + 1
            if count < self.strikes:
                looked_dead[job.job_id] = count
                logger.warning(
                    {
                        "message": f"FL job looks dead ({count} of {self.strikes} checks)",
                        "model": job.model_id,
                        "net": job.net_name,
                        "reason": reason,
                    }
                )
                continue
            try:
                if _fail_job(job, reason, session):
                    failed.append(job.model_id)
            except Exception:
                # One model that cannot be failed must not keep the others wedged. Keeping its count retries it on
                # the next tick.
                logger.exception({"message": "Could not fail a dead FL job's model", "model": job.model_id})
                session.rollback()
                looked_dead[job.job_id] = count

        # Jobs that looked alive this tick, or are no longer watched, start from zero next time.
        self._looked_dead = looked_dead
        return failed


@cache
def _watchdog() -> JobWatchdog:
    """The process's one watchdog: its strike counts must survive between ticks."""
    settings = get_settings()
    return JobWatchdog(
        strikes=settings.FL_JOB_WATCHDOG_STRIKES,
        prepare_timeout=timedelta(minutes=settings.FL_JOB_PREPARE_TIMEOUT_MINUTES),
    )


def watch_fl_jobs_scheduled_task() -> None:
    """Scheduled task: check every running FL job once. Never raises, so one bad tick cannot stop the next."""
    try:
        with Session(get_engine()) as session:
            _watchdog().tick(session, datetime.utcnow())
    except Exception:
        logger.exception("FL job watchdog tick failed")
