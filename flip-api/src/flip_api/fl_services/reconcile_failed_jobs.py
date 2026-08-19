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
in-flight job's FL API for that job's status and, on ``FAILED``, records the backend's log
tail against the model and drives it to ``ERROR`` — which releases the net through
``update_model_status``'s existing terminal-status path.

Deliberately narrow, because the sweep's own errors would be much worse than the bug it
fixes:

* only ``FAILED`` acts. ``FINISHED`` is left alone — a run whose ServerApp has finished is
  routinely still uploading results, and the hub's own ``RESULTS_UPLOADED`` callback is the
  authority on that.
* only jobs the hub still considers in flight, whose model has not already reached a
  terminal status, are polled at all; the model status is re-read after the (network) status
  call so a result landing mid-poll wins the race.
* everything is per-job best-effort: an unreachable FL API, a malformed response or a failed
  write is logged and the sweep moves to the next job.

Scope: this covers the **ServerApp**. A ClientApp that dies at a trust logs to that trust's
SuperNode, which the hub cannot read, and is out of scope here.
"""

from typing import cast
from uuid import UUID

from sqlmodel import Session, col, select

from flip_api.db.database import get_engine
from flip_api.db.models.main_models import FLJob, FLNets, FLScheduler, Model
from flip_api.domain.schemas.status import FLJobStatus, JobStatus, ModelStatus
from flip_api.fl_services.services.fl_service import fetch_run_logs, get_backend_job_status
from flip_api.model_services.services.model_service import add_log, update_model_status
from flip_api.utils.logger import logger

# Model statuses that mean "the hub is still waiting on this run". A model outside this set
# has already been resolved (by the run itself, by an abort, or by an earlier sweep), so its
# job is not polled — which also makes the sweep idempotent: the ERROR it writes takes the
# model out of the set.
_IN_FLIGHT_MODEL_STATUSES = (ModelStatus.INITIATED, ModelStatus.PREPARED, ModelStatus.RUNNING)

# Second cap on the stored log tail. fl-api-flower already truncates before returning, so
# this only guards against an FL API that doesn't — a runaway body must not become a
# runaway `fl_logs` row.
_MAX_STORED_LOG_CHARS = 8000

# Only fl-api-flower serves /run_logs today, so the fallback names the Flower command; an
# NVFLARE net simply always lands here.
_MANUAL_FALLBACK_HINT = (
    "The FL run log could not be retrieved from the FL API. On a Flower net, run "
    "`flwr log <run-id> <superlink> --show` inside the FL API container to read it."
)


def _failure_message(fl_backend_job_id: str, run_log: str | None) -> str:
    """Compose the activity-feed text for a run that failed after submission.

    Args:
        fl_backend_job_id (str): The backend-assigned job id, quoted so an operator can
            take it straight to ``flwr log`` / the FL API.
        run_log (str | None): The backend's log tail, when it could be retrieved.

    Returns:
        str: The text stored on the model's failed ``fl_logs`` row.
    """
    header = f"Training failed: FL run {fl_backend_job_id} ended in a failed state without reporting a result."
    if not run_log or not run_log.strip():
        return f"{header} {_MANUAL_FALLBACK_HINT}"
    return f"{header}\n\nEnd of the FL run log:\n{run_log.strip()[-_MAX_STORED_LOG_CHARS:]}"


def _report_failed_run(model_id: UUID, fl_backend_job_id: str, endpoint: str, session: Session) -> None:
    """Record a failed run against its model and move the model to ``ERROR``.

    The log row is written before the status change so the activity feed reads
    cause-then-verdict, matching the prepare-failure path in ``prepare_and_start_training``.
    ``update_model_status(ERROR)`` completes the FL job and frees the net.

    Args:
        model_id (UUID): The model whose run failed.
        fl_backend_job_id (str): The backend-assigned job id of the failed run.
        endpoint (str): The FL API endpoint of the net the job ran on.
        session (Session): SQLModel session.

    Returns:
        None
    """
    run_log = fetch_run_logs(endpoint, fl_backend_job_id)
    add_log(model_id, _failure_message(fl_backend_job_id, run_log), session, success=False)
    update_model_status(model_id, ModelStatus.ERROR, session)
    logger.error(f"FL run {fl_backend_job_id} for model {model_id} failed; model set to ERROR.")


def reconcile_failed_fl_jobs(session: Session) -> int:
    """Check every in-flight FL job with the backend and resolve the ones that failed.

    Args:
        session (Session): SQLModel session.

    Returns:
        int: The number of models moved to ``ERROR`` by this pass.
    """
    # One row per in-flight job, joined through its *own* scheduler so a model that has
    # been retried is checked against the net its current job is pinned to rather than
    # whichever net a stale job once used.
    statement = (
        select(FLJob.id, FLJob.model_id, FLJob.fl_backend_job_id, FLNets.endpoint)
        .join(FLScheduler, col(FLScheduler.job_id) == col(FLJob.id))
        .join(FLNets, col(FLNets.id) == col(FLScheduler.net_id))
        .join(Model, col(Model.id) == col(FLJob.model_id))
        .where(
            FLJob.status == JobStatus.IN_PROGRESS,
            col(FLJob.fl_backend_job_id).is_not(None),
            col(Model.status).in_(_IN_FLIGHT_MODEL_STATUSES),
        )
    )
    jobs = session.exec(statement).all()
    if not jobs:
        return 0

    reported = 0
    for job_id, model_id, nullable_backend_job_id, endpoint in jobs:
        # Non-null by the WHERE clause above; the column is nullable until submission.
        fl_backend_job_id = cast(str, nullable_backend_job_id)
        try:
            backend_status = get_backend_job_status(endpoint, fl_backend_job_id)
            if backend_status != FLJobStatus.FAILED:
                continue

            # The status call above went over the network; a result or an abort can have
            # landed in the meantime. Re-read the model's status (a column select, so it
            # bypasses the session identity map and sees the latest committed value) and
            # defer to whatever won.
            current_status = session.exec(select(col(Model.status)).where(Model.id == model_id)).one_or_none()
            if current_status not in _IN_FLIGHT_MODEL_STATUSES:
                logger.info(
                    f"FL run {fl_backend_job_id} reported FAILED but model {model_id} has already "
                    f"settled as {current_status}; leaving it alone."
                )
                continue

            _report_failed_run(model_id, fl_backend_job_id, endpoint, session)
            reported += 1
        except Exception as e:
            # One unreachable net (or one poisoned write) must not stop the other jobs being
            # checked. Roll back first: a failed write leaves the transaction unusable, and
            # every subsequent row would raise on it.
            session.rollback()
            logger.error(
                f"Failed to reconcile FL job {job_id} (backend id {fl_backend_job_id}): {type(e).__name__}: {e}"
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
        logger.error(f"Error in scheduled FL job reconcile: {type(e).__name__}: {e}")
