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

"""Surface FL runs that fail after the hub has already submitted them (FLIP#1001).

``start_training`` submits a job, stores the backend's job id on ``FLJob`` and never asks
about it again. Everything the hub knows about a run after that arrives *from* the run —
the ServerApp reports rounds, metrics and its final status through the ``flip`` package.
A run that dies before any of that happens (an ImportError at ServerApp module scope is
the canonical case) therefore reports nothing at all, and the model sits at ``INITIATED``
forever with the cause visible only to whoever knows to run ``flwr log`` inside the FL API
container.

This sweep closes that gap from the one side that can: the hub already holds the job id,
the net the job is pinned to, and the authority to move the model. Each tick it asks every
in-flight job's FL API for that job's status and, when the run can never report, records
the backend's log tail against the model and drives it to ``ERROR`` — which releases the
net through ``update_model_status``'s existing terminal-status path.

That same ``/list_jobs`` response also carries ``status_details`` — the backend's own one-line
explanation, where it has one — so the feed row can lead with the cause rather than opening on
the per-run dependency install that dominates a Flower log tail. Flower fills it; NVFLARE has
no equivalent native field and leaves it unset.

Three conditions act, all meaning "this run will never report":

* the backend lists the job as ``FAILED``;
* the backend lists the job as ``STOPPED`` while the hub's own job row is still
  ``IN_PROGRESS``. The hub's abort path dequeues (``DELETED``) the job *before* it asks the
  backend to stop the run, so a still-in-progress job whose run is stopped can only have
  been stopped out of band — ``flwr stop`` in the FL API container, the FLARE admin
  console — and no one will report for it;
* the backend does not list the job at all and it was submitted more than
  ``FL_JOB_UNLISTED_GRACE_MINUTES`` ago. A SuperLink keeps run state in memory by default,
  so a restart forgets every run — the exact silent death this sweep exists for, just with
  the evidence gone too. The grace period keeps a transient listing hiccup from erroring a
  healthy run.

Deliberately narrow beyond that, because the sweep's own errors would be much worse than
the bug it fixes:

* ``FINISHED`` is left alone — a run whose ServerApp has finished is routinely still
  uploading results, and the hub's own ``RESULTS_UPLOADED`` callback is the authority on
  that. Past the grace period it is logged, since a model still waiting on a run its backend
  has finished means the results callback never landed. ``UNKNOWN`` (a native status the
  adapter's map does not recognise) is left alone and logged — it is exactly the case where
  acting would be guessing.
* the model's and the job's statuses are re-read after the (network) status call, so a
  result, or the hub's own abort, landing mid-poll wins the race.
* a model that has been retried has a newer job; a dead *older* job frees its own net and
  leaves the model — whose fate belongs to the retry — alone. (``update_model_status`` acts
  on the model's newest job, so erroring the model here would complete the healthy retry,
  the hazard ``fl_scheduler_service`` guards against with the same check.)
* everything is per-job best-effort: an unreachable FL API, a malformed response or a failed
  write is logged and the sweep moves to the next job.

One more state is swept, without polling: a model already terminal whose job is still
``IN_PROGRESS`` and its net ``BUSY``. ``update_model_status`` commits the status and then
``update_fl_scheduler`` commits the job/net release separately; a failure between the two
leaves a net nothing else revisits. The sweep releases it.

Scope: this covers the server side of a run — Flower's ServerApp, NVFLARE's server job. A
client-side death (a ClientApp or fl-client executor at a trust) logs at that trust, which
the hub cannot read, and is out of scope here.
"""

from datetime import datetime, timedelta
from typing import NamedTuple
from uuid import UUID

import httpx
from sqlmodel import Session, col, select

from flip_api.config import get_settings
from flip_api.db.database import get_engine
from flip_api.db.models.main_models import FLJob, FLNets, FLScheduler, Model
from flip_api.domain.schemas.status import FLJobStatus, JobStatus, ModelStatus, NetStatus
from flip_api.domain.schemas.types import FLBackend
from flip_api.fl_services.services.fl_service import RunLogTail, fetch_run_logs, get_backend_job_metadata
from flip_api.model_services.services.model_service import add_log, update_model_status
from flip_api.utils.logger import logger

# Model statuses that mean "the hub is still waiting on this run". A model outside this set
# has already been resolved (by the run itself, by an abort, or by an earlier sweep).
_IN_FLIGHT_MODEL_STATUSES = (ModelStatus.INITIATED, ModelStatus.PREPARED, ModelStatus.RUNNING)

# Second cap on the stored log tail. fl-api-flower already truncates before returning, so
# this only guards against an FL API that doesn't — a runaway body must not become a
# runaway `fl_logs` row.
_MAX_STORED_LOG_CHARS = 8000

# Where the log is when the sweep could not produce it, per backend. The feed row is read
# by the model owner, who has no shell on the hub, so it names the place and leaves the
# reading to a platform administrator. No hub service sets `container_name`, so the
# container names are compose's own for the default dev stack (`deploy` project); on ECS
# there is no docker and the same output is the task's CloudWatch log group.
_MANUAL_FALLBACK_HINTS: dict[FLBackend, str] = {
    FLBackend.FLOWER: (
        "The FL run log could not be retrieved from the FL API. A platform administrator can "
        "read it with `flwr log <run-id> local --show` inside the net's FL API container "
        "(`deploy-fl-api-net-<n>-1` on the default dev stack)."
    ),
    # fl-api-base serves no /run_logs yet. NVFLARE does keep the server job log (`log.txt`
    # in the run dir, zipped into the job store's `workspace` once the job finishes, and
    # served by the FLARE admin API's `get_job_logs(<job-id>, "server")`), so a future
    # fl-api-base endpoint has somewhere to read from; until then the same text is in the
    # fl-server container's output.
    FLBackend.NVFLARE: (
        "The FL run log could not be retrieved from the FL API (the NVFLARE FL API serves no "
        "run-log endpoint yet). A platform administrator can read the failure in the net's "
        "fl-server container output (`docker logs deploy-fl-server-net-<n>-1` on the default "
        "dev stack; the fl-server task's CloudWatch log group on ECS), or fetch the job's "
        "server log through the FLARE admin API (`get_job_logs`)."
    ),
}


class InFlightJob(NamedTuple):
    """One row of the sweep's selection: a submitted job the hub has not yet resolved.

    The SQL guarantees ``fl_backend_job_id`` is set (the job has been submitted) and the
    job is ``IN_PROGRESS``; ``model_status`` is what the model said at selection time and
    may be terminal (the leaked-net case).
    """

    job_id: UUID
    model_id: UUID
    fl_backend_job_id: str
    started: datetime | None
    endpoint: str
    fl_backend: FLBackend
    model_status: ModelStatus


def _failure_message(
    fl_backend_job_id: str, status_details: str | None, run_log: RunLogTail | None, fl_backend: FLBackend
) -> str:
    """Compose the activity-feed text for a run that failed after submission.

    ``status_details`` leads when the backend supplied one, because the log tail buries the
    cause: a Flower run log opens with the per-run ``uv sync``, whose single ``Installed:
    [...]`` line is most of the stored row, leaving the traceback below the fold of the feed
    panel. The one-liner is the same exception the traceback ends with, so putting it at the
    top costs a line and saves the reader the scroll. The tail still follows — it carries the
    file and line number, which the one-liner does not.

    Args:
        fl_backend_job_id (str): The backend-assigned job id, quoted so an operator can
            take it straight to ``flwr log`` / the FL API.
        status_details (str | None): The backend's own one-line cause, when it has one.
        run_log (RunLogTail | None): The backend's log tail when it could be retrieved;
            ``None`` when it could not, which is worded differently from a tail that came
            back empty.
        fl_backend (FLBackend): The net's backend, selecting the manual-fallback wording.

    Returns:
        str: The text stored on the model's failed ``fl_logs`` row.
    """
    header = f"Training failed: FL run {fl_backend_job_id} ended in a failed state without reporting a result."
    if status_details:
        header = f"{header}\n\nReported cause: {status_details}"
    if run_log is None:
        return f"{header}\n\n{_MANUAL_FALLBACK_HINTS[fl_backend]}"
    if not run_log.log.strip():
        return f"{header}\n\nThe FL API returned an empty log for this run."
    body = f"{header}\n\nEnd of the FL run log:\n{run_log.log.strip()[-_MAX_STORED_LOG_CHARS:]}"
    if run_log.truncated:
        body = (
            f"{body}\n\n(Log tail truncated. The full log can be read with `flwr log <run-id> local --show` "
            "inside the net's FL API container.)"
        )
    return body


def _stopped_message(fl_backend_job_id: str) -> str:
    """Compose the activity-feed text for a run stopped outside FLIP.

    Args:
        fl_backend_job_id (str): The backend-assigned job id of the stopped run.

    Returns:
        str: The text stored on the model's failed ``fl_logs`` row.
    """
    return (
        f"Training failed: FL run {fl_backend_job_id} was stopped outside FLIP (its backend reports it "
        "stopped, but no stop was requested through the platform) and will not report a result."
    )


def _unlisted_message(fl_backend_job_id: str, started: datetime) -> str:
    """Compose the activity-feed text for a run its own backend no longer lists.

    No log tail is offered: the backend that has forgotten the run (a SuperLink restart
    loses its in-memory run state) has forgotten its log with it.

    Args:
        fl_backend_job_id (str): The backend-assigned job id of the vanished run.
        started (datetime): When the hub started the job (naive UTC, ``FLJob.started``).

    Returns:
        str: The text stored on the model's failed ``fl_logs`` row.
    """
    return (
        f"Training failed: FL run {fl_backend_job_id} (started {started:%Y-%m-%d %H:%M} UTC) is no "
        "longer listed by its FL backend and never reported a result. The backend has likely "
        "restarted since submission, losing the run and its log."
    )


def _load_in_flight_jobs(session: Session) -> list[InFlightJob]:
    """Every submitted job the hub has not resolved, joined to its net and its model's status.

    Selects on the *job* being in progress, not on the model still being in flight: a model
    already terminal whose job is still ``IN_PROGRESS`` is the leaked-net state this sweep
    also repairs (see the module docstring), and would be invisible to a model-status filter.

    Args:
        session (Session): SQLModel session.

    Returns:
        list[InFlightJob]: One row per job, joined through its *own* scheduler so a model
            that has been retried is checked against the net its current job is pinned to
            rather than whichever net a stale job once used.
    """
    statement = (
        # SQLModel's typed select() overloads stop at four entities; the same ignore is
        # used for the six-column select in model_service._run_trusts_by_model.
        select(  # type: ignore[call-overload]
            FLJob.id,
            FLJob.model_id,
            FLJob.fl_backend_job_id,
            FLJob.started,
            FLNets.endpoint,
            FLNets.fl_backend,
            Model.status,
        )
        .join(FLScheduler, col(FLScheduler.job_id) == col(FLJob.id))
        .join(FLNets, col(FLNets.id) == col(FLScheduler.net_id))
        .join(Model, col(Model.id) == col(FLJob.model_id))
        .where(
            FLJob.status == JobStatus.IN_PROGRESS,
            col(FLJob.fl_backend_job_id).is_not(None),
        )
    )
    return [InFlightJob(*row) for row in session.exec(statement).all()]


def _reread_statuses(session: Session, job: InFlightJob) -> tuple[ModelStatus, JobStatus] | None:
    """The model's and the job's statuses as committed right now.

    A column select rather than a ``session.get``: it bypasses the session identity map and
    sees the latest committed value, which is the point — the caller has just spent time on
    the network and needs to know whether a result or an abort landed meanwhile.

    Args:
        session (Session): SQLModel session.
        job (InFlightJob): The job whose model and row to re-read.

    Returns:
        tuple[ModelStatus, JobStatus] | None: The two statuses, or ``None`` if either row is
            gone.
    """
    row = session.exec(
        select(col(Model.status), col(FLJob.status))
        .join(FLJob, col(FLJob.model_id) == col(Model.id))
        .where(Model.id == job.model_id, FLJob.id == job.job_id)
    ).one_or_none()
    return (row[0], row[1]) if row is not None else None


def _is_models_newest_job(session: Session, job: InFlightJob) -> bool:
    """Whether ``job`` is the model's most recent non-deleted job.

    ``update_model_status``'s terminal path completes the model's *newest* non-deleted job
    and frees its net. When a model has been re-initiated over a still-in-flight older job,
    that newest job is the healthy retry — the same check ``fl_scheduler_service`` makes
    before erroring a model off an old queued job.

    Args:
        session (Session): SQLModel session.
        job (InFlightJob): The job being reconciled.

    Returns:
        bool: True when no newer non-deleted job exists for the model.
    """
    newest = session.exec(
        select(FLJob.id)
        .where(FLJob.model_id == job.model_id, FLJob.status != JobStatus.DELETED)
        .order_by(col(FLJob.created).desc())
        .limit(1)
    ).first()
    return newest == job.job_id


def _release_job(job: InFlightJob, session: Session) -> None:
    """Complete one job and free the net it holds, without touching its model.

    The job-scoped twin of ``update_fl_scheduler`` (which is model-scoped and acts on the
    model's newest job). Used where the model is not this sweep's to move: a stale job of a
    retried model, or a model already terminal whose release never committed.

    Args:
        job (InFlightJob): The job to complete.
        session (Session): SQLModel session.

    Returns:
        None
    """
    fl_job = session.get(FLJob, job.job_id)
    if fl_job is not None:
        fl_job.status = JobStatus.COMPLETED
        fl_job.completed = datetime.utcnow()
        session.add(fl_job)
    scheduler = session.exec(select(FLScheduler).where(FLScheduler.job_id == job.job_id)).first()
    if scheduler is not None and scheduler.status == NetStatus.BUSY:
        scheduler.status = NetStatus.AVAILABLE
        session.add(scheduler)
    session.commit()


def _resolve_failure(job: InFlightJob, message: str, session: Session) -> bool:
    """Record a dead run against its model and move the model to ``ERROR``.

    The log row is added before the status change so the activity feed reads
    cause-then-verdict, matching the prepare-failure path in ``prepare_and_start_training``
    — but with ``transaction=session`` so it commits *with* ``update_model_status``'s own
    commit rather than on its own: a status write that fails must not leave an orphaned
    feed row for the next tick to duplicate. ``update_model_status(ERROR)`` then completes
    the model's newest job — this one, by the caller's check — and frees its net.

    ``update_model_status`` declines a late transition on a model the user has just stopped
    (it returns the model's current status without committing). The pending feed row is then
    rolled back here rather than left in the session, where a later job's commit in the same
    tick would flush it onto a model that was not erroring.

    Args:
        job (InFlightJob): The job whose run died.
        message (str): The activity-feed text explaining the failure.
        session (Session): SQLModel session.

    Returns:
        bool: True when the model was moved to ``ERROR``; False when a concurrent transition
            won and nothing was written.
    """
    add_log(job.model_id, message, session, transaction=session, success=False)
    updated = update_model_status(job.model_id, ModelStatus.ERROR, session)
    if updated != ModelStatus.ERROR:
        session.rollback()
        logger.info(
            f"FL run {job.fl_backend_job_id} for model {job.model_id} is dead, but the model moved to "
            f"{updated} concurrently; leaving it alone."
        )
        return False
    logger.error(f"FL run {job.fl_backend_job_id} for model {job.model_id} failed; model set to ERROR.")
    return True


def _verdict(job: InFlightJob, unlisted_grace: timedelta) -> str | None:
    """Ask the backend about ``job`` and decide whether its run can still report.

    Args:
        job (InFlightJob): The job to check.
        unlisted_grace (timedelta): How long an unlisted (or finished-but-silent) run is
            given before it is treated as dead.

    Returns:
        str | None: The activity-feed text to record when the run can never report, or
            ``None`` to leave the job alone this tick.
    """
    backend_job = get_backend_job_metadata(job.endpoint, job.fl_backend_job_id)
    backend_status = backend_job.status if backend_job else None
    age = datetime.utcnow() - job.started if job.started is not None else None
    past_grace = age is not None and age > unlisted_grace

    if backend_job is not None and backend_status == FLJobStatus.FAILED:
        run_log = fetch_run_logs(job.endpoint, job.fl_backend_job_id, job.fl_backend)
        return _failure_message(job.fl_backend_job_id, backend_job.status_details, run_log, job.fl_backend)
    if backend_status == FLJobStatus.STOPPED:
        return _stopped_message(job.fl_backend_job_id)
    if backend_status is None:
        # No record of a run the hub submitted. Past the grace that is a backend restart
        # (SuperLink run state is in-memory by default): the run can never report, so it
        # gets the same resolution as FAILED, minus the unfetchable log. Comfortably past
        # any submission race: `started` predates the submit that minted the backend id.
        # Within the grace it is a listing hiccup, retried next tick.
        return _unlisted_message(job.fl_backend_job_id, job.started) if past_grace and job.started else None
    if backend_status == FLJobStatus.FINISHED and past_grace:
        # The run's own RESULTS_UPLOADED callback is the authority on a finished run — but a
        # model still waiting on one this long after submission means that callback never
        # landed. Not acted on; said out loud.
        logger.warning(
            f"FL run {job.fl_backend_job_id} for model {job.model_id} finished on its backend but never "
            f"reported a result to the hub (started {job.started}); leaving it alone."
        )
    elif backend_status == FLJobStatus.UNKNOWN:
        # An unmapped native status: acting would be guessing. The adapter warns in its own
        # container; the hub's log has to carry it too, or a model stuck on it looks like
        # nothing at all from here.
        logger.warning(
            f"FL run {job.fl_backend_job_id} for model {job.model_id} reports a status this hub cannot "
            "interpret (UNKNOWN); leaving it alone."
        )
    # PENDING / RUNNING: alive. FINISHED within the grace: still uploading.
    return None


def reconcile_failed_fl_jobs(session: Session) -> int:
    """Check every in-flight FL job with the backend and resolve the ones that failed.

    Args:
        session (Session): SQLModel session.

    Returns:
        int: The number of models moved to ``ERROR`` by this pass.
    """
    jobs = _load_in_flight_jobs(session)
    if not jobs:
        return 0

    unlisted_grace = timedelta(minutes=get_settings().FL_JOB_UNLISTED_GRACE_MINUTES)
    reported = 0
    for job in jobs:
        try:
            if job.model_status not in _IN_FLIGHT_MODEL_STATUSES:
                # The model settled but its job and net were never released (the release is
                # a separate commit from the status). Nothing else revisits this state.
                _release_job(job, session)
                logger.warning(
                    f"Released net held by job {job.job_id} (backend id {job.fl_backend_job_id}) of model "
                    f"{job.model_id}, which is already {job.model_status}."
                )
                continue

            message = _verdict(job, unlisted_grace)
            if message is None:
                continue

            # The calls above went over the network; a result or an abort can have landed
            # in the meantime. Defer to whatever won: a model no longer in flight has been
            # resolved by someone, and a job no longer IN_PROGRESS has been dequeued by the
            # hub's own abort, which stops the run *after* dequeueing and settles the model
            # itself.
            current = _reread_statuses(session, job)
            if current is None:
                continue
            model_status, job_status = current
            if job_status != JobStatus.IN_PROGRESS:
                logger.info(
                    f"FL run {job.fl_backend_job_id} is dead but job {job.job_id} is now {job_status} — the "
                    "hub's own abort owns it; leaving it alone."
                )
                continue
            if model_status not in _IN_FLIGHT_MODEL_STATUSES:
                logger.info(
                    f"FL run {job.fl_backend_job_id} is dead but model {job.model_id} has already settled as "
                    f"{model_status}; leaving it alone."
                )
                continue

            if not _is_models_newest_job(session, job):
                # The model was re-initiated over this job. Its fate belongs to the retry;
                # this job just needs to give its net back.
                _release_job(job, session)
                logger.warning(
                    f"Released net held by stale job {job.job_id} (backend id {job.fl_backend_job_id}): "
                    f"model {job.model_id} has since been retried, so the model is left to its newer job."
                )
                continue

            if _resolve_failure(job, message, session):
                reported += 1
        except Exception as e:
            # One unreachable net (or one poisoned write) must not stop the other jobs being
            # checked. Roll back first: a failed write leaves the transaction unusable, and
            # every subsequent row would raise on it.
            session.rollback()
            detail = f"{type(e).__name__}: {e}"
            if isinstance(e, httpx.HTTPStatusError):
                # The FL API's own `detail` (e.g. the flwr stderr) is the reason; without it
                # the cause lives only in the fl-api container.
                detail = f"{detail} — response: {e.response.text[:300]}"
            logger.error(
                f"Failed to reconcile FL job {job.job_id} (backend id {job.fl_backend_job_id}): {detail}",
                exc_info=True,
            )

    return reported


def reconcile_failed_fl_jobs_scheduled_task() -> None:
    """Scheduled entry point for :func:`reconcile_failed_fl_jobs`.

    Never raises: the sweep is a safety net, and a failure in it must not take down the
    background scheduler.
    """
    try:
        with Session(get_engine()) as db:
            reported = reconcile_failed_fl_jobs(db)
        if reported:
            logger.info(f"FL job reconcile marked {reported} model(s) as errored.")
    except Exception as e:
        logger.error(f"Error in scheduled FL job reconcile: {type(e).__name__}: {e}", exc_info=True)
