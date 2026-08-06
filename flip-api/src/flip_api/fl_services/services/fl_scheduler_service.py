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

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Column
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

from flip_api.db.models.main_models import (
    FLJob,
    FLKitSlot,
    FLLogs,
    FLNets,
    FLScheduler,
    Model,
    Queries,
)
from flip_api.domain.interfaces.fl import (
    IJobResponse,
    INetDetails,
    IRequiredTrainingInformation,
    ISchedulerResponse,
)
from flip_api.domain.schemas.status import (
    JobStatus,
    ModelStatus,
    NetStatus,
)
from flip_api.domain.schemas.types import FLBackend, FLLogEvent
from flip_api.fl_services.services.fl_service import (
    _raise_if_job_aborted,
    bundle_flower_application,
    bundle_nvflare_application,
    get_bundle_urls,
    start_training,
    validate_client_availability,
)
from flip_api.model_services.services.model_service import add_log, update_model_status, validate_trust_ids
from flip_api.utils.exceptions import DatabaseError, JobAbortedError, NotFoundError
from flip_api.utils.logger import logger


def remove_job(job_id: UUID, session: Session) -> None:
    """
    Sets the job status to DELETED and clears the started timestamp.

    Args:
        job_id (UUID): The ID of the job to remove.
        session (Session): SQLModel session.

    Returns:
        None

    Raises:
        flip_api.utils.exceptions.NotFoundError: If no ``FLJob`` exists with the given ID.
        DatabaseError: If the update fails at the DB layer.
    """
    logger.info(f"Reverting job pickup: {job_id}")
    try:
        job = session.get(FLJob, job_id)
        if not job:
            logger.error(f"FLJob with id {job_id} not found")
            raise NotFoundError(f"FLJob with id {job_id} not found")

        job.status = JobStatus.DELETED
        job.started = None

        session.commit()

        logger.info("Reverted job pickup")

    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Error reverting job pickup: {e}")
        raise DatabaseError("Error reverting job pickup") from e


def remove_job_from_queue(model_id: UUID, session: Session) -> None:
    """
    Sets the job status to DELETED for all jobs associated with the given model ID.

    Args:
        model_id (UUID): The model ID whose jobs are to be removed.
        session (Session): SQLModel session.

    Returns:
        None

    Raises:
        DatabaseError: If the update fails at the DB layer.
    """
    logger.info(f"Setting job status for model ({model_id}) to {JobStatus.DELETED}")
    try:
        statement = select(FLJob).where(FLJob.model_id == model_id, FLJob.status != JobStatus.DELETED)
        jobs = session.exec(statement).all()

        if not jobs:
            logger.warning(f"No active job found for model id: {model_id}")
            return

        for job in jobs:
            job.status = JobStatus.DELETED
            job.completed = datetime.utcnow()

        session.commit()
        logger.info("Set job(s) as deleted")

        # Deleting a queued job re-ranks everything behind it.
        log_queue_positions(session)

    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Error removing job from queue: {e}")
        raise DatabaseError("Error removing job from queue") from e


def log_queue_positions(session: Session) -> None:
    """Emit one typed activity row per queued job whose queue position changed.

    The FL queue is ``FLJob`` rows in ``QUEUED`` status, ordered by ``created``
    ascending — exactly the order ``check_for_queued_jobs`` picks from, so
    position 1 is the next model to start when a net frees up. For each queued
    job the last logged ``QUEUE_POSITION`` row is compared (keyed by job id, so
    a re-initiated model always re-logs its first position); a row is written
    only when the position is new or changed, making the function idempotent —
    callers invoke it after any queue mutation without worrying about noise.
    Rows are batched into a single commit so a deep queue never costs O(N)
    commits per mutation.

    Idempotence holds for serialized callers only: emit-on-change is a
    read-then-write with nothing locked, so concurrent calls (the scheduler
    tick on its ``BackgroundScheduler`` thread vs a request thread, or a
    second flip-api replica) can both read the same prior rows and both
    write, leaving duplicate — or transiently contradictory — feed rows.
    That is tolerated: positions are a display nicety and the next mutation
    self-heals. The hub currently runs one flip-api replica with a single
    Uvicorn worker (ECS ``desired_count = 1``, no autoscaling; ``fastapi
    run`` without ``--workers``), so only in-process thread interleaving can
    race. Scaling flip-api out multiplies the emission sites per tick — close
    the race then with ``SELECT ... FOR UPDATE`` on the queued jobs or a
    partial unique index over the ``details`` (job_id, position) pair.

    Positions are a display nicety: any failure is logged, the batch is rolled
    back, and nothing is raised — an emission problem can never wedge the
    scheduler tick or the caller's queue mutation. **Precondition:** call this
    only after the caller has committed its own mutation; the failure-path
    rollback would otherwise discard the caller's uncommitted state.

    Args:
        session (Session): The database session.

    Returns:
        None
    """
    try:
        # id is the tiebreak on identical created timestamps so this ranking,
        # queued_positions_by_model and check_for_queued_jobs can never disagree.
        queued_jobs = session.exec(
            select(FLJob)
            .where(FLJob.status == JobStatus.QUEUED)
            .order_by(cast(Column, FLJob.created).asc(), cast(Column, FLJob.id).asc())
        ).all()
        if not queued_jobs:
            return

        # DISTINCT ON collapses each job's history to its newest row inside
        # Postgres, and only the compared details payload is fetched — a
        # model's QUEUE_POSITION rows accumulate for life (each drain of a
        # depth-N queue writes O(N^2) rows in total), so materialising every
        # historical row as a full ORM object would grow monotonically with
        # usage. If this scan ever shows up in slow-query logs regardless, the
        # next lever is an index on (model_id, event_type) in a future
        # Alembic revision.
        prior_job_id = col(FLLogs.details)["job_id"].astext
        prior_details = session.exec(
            select(cast(Column, FLLogs.details))
            .where(
                col(FLLogs.model_id).in_([job.model_id for job in queued_jobs]),
                FLLogs.event_type == FLLogEvent.QUEUE_POSITION.value,
            )
            .distinct(prior_job_id)
            .order_by(prior_job_id, cast(Column, FLLogs.log_date).desc())
        ).all()
        # Keyed by job id (not model id): a model can hold two QUEUED jobs (no
        # uniqueness on FLJob.model_id), and a model-keyed lookup would let the
        # newest row suppress one job while forcing the other to re-emit forever.
        # setdefault keeps first-seen-newest as defense in depth should the
        # query ever hand back more than one row per job.
        last_details_by_job: dict[str, dict] = {}
        for row_details in prior_details:
            row_job_id = (row_details or {}).get("job_id")
            if isinstance(row_job_id, str):
                last_details_by_job.setdefault(row_job_id, row_details or {})

        emitted = False
        for position, job in enumerate(queued_jobs, start=1):
            if last_details_by_job.get(str(job.id), {}).get("position") == position:
                continue
            # A non-None transaction makes add_log skip its per-row commit; the
            # whole batch lands in the single commit below.
            add_log(
                job.model_id,
                None,
                session,
                event_type=FLLogEvent.QUEUE_POSITION.value,
                details={"position": position, "job_id": str(job.id)},
                transaction=session,
            )
            emitted = True
        if emitted:
            session.commit()
    except Exception:
        logger.exception("Failed to emit queue-position logs")
        try:
            session.rollback()
        except Exception:
            # A session whose rollback failed raises PendingRollbackError on
            # every subsequent use — handed back to run_jobs_core it would fail
            # the training start and wedge the net. invalidate() discards the
            # broken connection without running ROLLBACK on it, leaving the
            # session reusable on a fresh connection.
            logger.exception("Failed to roll back queue-position emission; invalidating session")
            session.invalidate()


def revert_scheduler_pickup(scheduler_id: UUID, session: Session) -> None:
    """
    Sets the scheduler status to AVAILABLE and clears the job_id.

    Args:
        scheduler_id (UUID): The ID of the scheduler to revert.
        session (Session): SQLModel session.

    Returns:
        None

    Raises:
        flip_api.utils.exceptions.NotFoundError: If no ``FLScheduler`` exists with the given ID.
        DatabaseError: If the update fails at the DB layer.
    """
    logger.info("Reverting scheduler pickup")
    try:
        scheduler = session.get(FLScheduler, scheduler_id)
        if not scheduler:
            logger.error(f"FLScheduler with id {scheduler_id} not found")
            raise NotFoundError(f"FLScheduler with id {scheduler_id} not found")

        scheduler.status = NetStatus.AVAILABLE
        scheduler.job_id = None

        session.commit()

        logger.info("Reverted scheduler pickup")

    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Error reverting scheduler pickup: {e}")
        raise DatabaseError("Error reverting scheduler pickup") from e


def release_scheduler_for_model(model_id: UUID, session: Session) -> int:
    """
    Free any BUSY scheduler still pinned to one of this model's FL jobs.

    ``FLScheduler.job_id`` survives a job's flip to ``DELETED`` (``remove_job`` /
    ``remove_job_from_queue`` only change ``FLJob.status``), so the scheduler is found by joining
    through the model's job rows — no need to capture job ids before dequeueing. Only BUSY
    schedulers are touched, so a scheduler already reassigned to another model's job is never
    released by mistake. Idempotent: no matching scheduler is a no-op.

    Args:
        model_id (UUID): The model whose scheduler pickup should be reverted.
        session (Session): SQLModel session.

    Returns:
        int: Number of schedulers released.

    Raises:
        DatabaseError: If the lookup or the release fails at the DB layer.
    """
    try:
        statement = (
            select(FLScheduler)
            .join(FLJob)
            .where(FLJob.model_id == model_id, FLScheduler.status == NetStatus.BUSY)
        )
        schedulers = session.exec(statement).all()
    except SQLAlchemyError as e:
        logger.error(f"Error looking up BUSY scheduler for model {model_id}: {e}")
        raise DatabaseError("Error looking up BUSY scheduler for model") from e

    for scheduler in schedulers:
        revert_scheduler_pickup(scheduler.id, session)

    return len(schedulers)


def get_net_by_model_id(model_id: UUID, session: Session) -> INetDetails:
    """
    Get information for a net by model ID.

    Args:
        model_id (UUID): The model ID.
        session (Session): SQLModel session.

    Returns:
        INetDetails: Details of the net.

    Raises:
        flip_api.utils.exceptions.NotFoundError: If no net is associated with the given ``model_id``.
        DatabaseError: If the query fails at the DB layer.
    """
    logger.info("Getting the net endpoint via its model ID...")
    try:
        statement = (
            select(FLNets.endpoint, FLNets.name, FLNets.fl_backend)
            .join(FLScheduler)
            .join(FLJob)
            .where(FLJob.model_id == model_id)
            .limit(1)
        )
        result = session.exec(statement).first()

        if not result or not result[0]:
            logger.error(f"Net not found for model ID: {model_id}")
            raise NotFoundError(f"Net not found for model ID: {model_id}")

        endpoint, name, fl_backend = result
        # The auto-mapped Enum column yields a real FLBackend at runtime; sqlmodel just types a
        # single-column select as str, so assert the type back.
        return INetDetails(endpoint=endpoint, name=name, fl_backend=cast("FLBackend", fl_backend))

    except SQLAlchemyError as e:
        logger.error(f"Error getting net by model ID: {e}")
        raise DatabaseError("Error getting net by model ID") from e


def get_net_by_name(name: str, session: Session) -> INetDetails | None:
    """
    Get information for a net by name

    Args:
        name (str): Name of the net
        session (Session): SQLModel session

    Returns:
        INetDetails | None: Details of the net or None if not found

    Raises:
        DatabaseError: If the query fails at the DB layer.
    """
    logger.info(f"Getting {name} info from db...")

    try:
        statement = select(FLNets.endpoint, FLNets.name, FLNets.fl_backend).where(FLNets.name == name)
        result = session.exec(statement).first()

        logger.info(f"Query result: {result}")

        if not result:
            logger.error(f"{name} could not be found")
            return None

        endpoint, net_name, fl_backend = result
        return INetDetails(endpoint=endpoint, name=net_name, fl_backend=cast("FLBackend", fl_backend))

    except SQLAlchemyError as e:
        logger.error(f"Database error while getting net by name: {e}")
        raise DatabaseError("Database error while getting net by name") from e


def get_slot_names_by_trust_ids(trust_ids: list[UUID], session: Session) -> dict[UUID, str]:
    """Map each trust id to the slot_name of its bound FL kit slot.

    The FL protocol identifies participants by the slot identity (the CN baked into the
    kit's cert), which is independent of the trust's display name on the hub. Callers that
    need to talk to / compare against an FL participant must look up the slot name rather
    than using ``Trust.name`` — admin-chosen display names can change without rotating
    the kit, and don't carry into the FL protocol.

    Args:
        trust_ids (list[UUID]): Trust ids to resolve. Empty input → empty mapping.
        session (Session): SQLModel session.

    Returns:
        dict[UUID, str]: ``trust_id → slot_name``. Trusts without an assigned slot are
        absent from the result; callers should treat a miss as "no FL identity yet".
    """
    if not trust_ids:
        return {}
    rows = session.exec(
        select(FLKitSlot.assigned_to_trust_id, FLKitSlot.slot_name).where(
            col(FLKitSlot.assigned_to_trust_id).in_(trust_ids)
        )
    ).all()
    return {trust_id: slot_name for trust_id, slot_name in rows if trust_id is not None}


def get_nets(session: Session) -> list[INetDetails]:
    """
    Fetches all nets from the database.

    Args:
        session (Session): The database session.

    Returns:
        list[INetDetails]: A list of all nets.

    Raises:
        flip_api.utils.exceptions.NotFoundError: If no nets are registered in the database.
        DatabaseError: If the query fails at the DB layer.
    """
    logger.info("Getting net info from db...")
    try:
        statement = select(FLNets.endpoint, FLNets.name, FLNets.fl_backend)
        results = session.exec(statement).all()

        if not results:
            error_message = "No db response returned when querying for nets"
            logger.error(error_message)
            raise NotFoundError(error_message)

        return [
            INetDetails(endpoint=endpoint, name=name, fl_backend=cast("FLBackend", fl_backend))
            for endpoint, name, fl_backend in results
        ]

    except SQLAlchemyError as e:
        logger.error(f"Error getting nets: {e}")
        raise DatabaseError("Error getting nets") from e


def resolve_backend(session: Session, net: INetDetails | None = None) -> FLBackend:
    """Resolve the active FL backend at runtime from the nets (never from a static env var).

    Every net carries a non-null ``fl_backend`` set at seed time from FL_BACKEND. That seeded
    value is canonical — there is no runtime reconciliation — so resolution always reads the DB.

    Args:
        session (Session): SQLModel session (used when no net is given).
        net (INetDetails | None): When given, use this net's backend (the job is already pinned
            to it). When ``None`` (e.g. at model creation, before scheduling), use any net's
            backend — all nets run the same backend in single-backend mode.

    Returns:
        FLBackend: The resolved backend (``nvflare`` or ``flower``).

    Raises:
        ValueError: If no FL nets are registered at all (empty NET_ENDPOINTS / misconfig).
    """
    if net is not None:
        return net.fl_backend

    backend = session.exec(select(FLNets.fl_backend)).first()
    if backend is not None:
        # Auto-mapped Enum column; sqlmodel types the single-column select as str, assert it back.
        return cast("FLBackend", backend)

    raise ValueError(
        "Cannot determine the active FL backend: no FL nets are registered. "
        "Check NET_ENDPOINTS and that seeding ran."
    )


def check_for_available_net(session: Session) -> ISchedulerResponse | None:
    """
    Checks for any available nets and marks one as busy if found.

    Args:
        session (Session): The database session.

    Returns:
        ISchedulerResponse | None: The scheduler response if an available net is found, otherwise None.

    Raises:
        DatabaseError: If the update fails at the DB layer.
    """
    logger.info("Checking for any available nets...")

    try:
        # Select one available scheduler
        scheduler_stmt = (
            select(FLScheduler)
            .where(FLScheduler.status == NetStatus.AVAILABLE)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

        scheduler = session.exec(scheduler_stmt).first()

        if scheduler:
            scheduler.status = NetStatus.BUSY
            session.commit()
            session.refresh(scheduler)
            return ISchedulerResponse(id=scheduler.id, netId=scheduler.net_id)

        return None

    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Error checking for available net: {e}")
        raise DatabaseError("Error checking for available net") from e


def check_for_queued_jobs(scheduler_id: UUID, session: Session) -> IJobResponse | None:
    """
    Checks for any queued jobs for a given scheduler.

    Args:
        scheduler_id (UUID): The ID of the scheduler to check.
        session (Session): The database session.

    Returns:
        IJobResponse | None: The job response if a queued job is found, otherwise None.

    Raises:
        flip_api.utils.exceptions.NotFoundError: If the scheduler referenced by ``scheduler_id`` cannot be found.
        DatabaseError: If the query or update fails at the DB layer.
        Exception: If the job references invalid trusts.
    """
    logger.info("Checking for any queued jobs...")

    try:
        # Find the earliest queued job. The id tiebreak on identical created
        # timestamps keeps the pickup order in exact agreement with the ranks
        # shown by log_queue_positions / queued_positions_by_model.
        job_stmt = (
            select(FLJob)
            .where(FLJob.status == JobStatus.QUEUED)
            .order_by(cast(Column, FLJob.created).asc(), cast(Column, FLJob.id).asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )

        job = session.exec(job_stmt).first()

        if not job:
            logger.info("No jobs queued, making the scheduler available again...")
            revert_scheduler_pickup(scheduler_id, session)
            return None

        # Update job status and start time
        job.status = JobStatus.IN_PROGRESS
        job.started = datetime.utcnow()

        job_trust_ids = [t.id for t in job.trusts]
        # Validate trusts. A job that references trusts not approved for its model can never run,
        # so retire it here rather than raising: the raise left the job QUEUED (the status write
        # above rolls back with the transaction), and because this query always picks the
        # globally-earliest queued job, the same unrunnable job was re-selected on every tick and
        # blocked every job behind it, on every net, indefinitely (FLIP#894). initiate_training now
        # rejects these at the boundary, so reaching this branch means a job predating that check
        # or one whose model approvals changed after it was queued.
        if not validate_trust_ids(job.model_id, job_trust_ids, session):
            logger.error(
                f"Job {job.id} references trust ids not approved for model {job.model_id}; "
                "retiring it so it cannot block the queue"
            )
            job.status = JobStatus.DELETED

            # A model can have been retried while this older job was waiting in the queue. Do not
            # transition the model to ERROR in that case: update_model_status(ERROR) releases the
            # latest non-deleted job for the model, which would silently complete the valid retry.
            active_job = session.exec(
                select(FLJob.id)
                .where(
                    FLJob.model_id == job.model_id,
                    col(FLJob.status).in_((JobStatus.QUEUED, JobStatus.IN_PROGRESS)),
                )
                .limit(1)
            ).first()
            if not active_job:
                update_model_status(job.model_id, ModelStatus.ERROR, session)
            add_log(
                job.model_id,
                "Training could not start: the selected trusts are not approved for this model.",
                session,
                success=False,
            )
            session.commit()
            revert_scheduler_pickup(scheduler_id, session)
            return None

        # Assign job to scheduler
        scheduler = session.get(FLScheduler, scheduler_id)
        if not scheduler:
            logger.error(f"Scheduler with id {scheduler_id} not found")
            raise NotFoundError(f"Scheduler with id {scheduler_id} not found")

        scheduler.job_id = job.id

        session.commit()

        return IJobResponse(id=job.id, model_id=job.model_id, trust_ids=job_trust_ids)

    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Error checking for queued jobs: {e}")
        revert_scheduler_pickup(scheduler_id, session)
        raise DatabaseError("Error checking for queued jobs") from e


def prepare_and_start_training(model_id: UUID, fl_job_id: UUID, trust_ids: list[UUID], session: Session) -> bool:
    """
    Prepares and starts the training process for a given model.

    Args:
        model_id (UUID): The ID of the model to train.
        fl_job_id (UUID): The ID of the federated learning job.
        trust_ids (list[UUID]): The trust ids participating in the training. Names are looked up
            from the `trust` table here — at the FL backend boundary, which is the only place
            the FL protocol's name-based addressing matters.
        session (Session): The database session.

    Returns:
        bool: True when the job was submitted to the fl-server; False when a concurrent abort
            (#787) deleted the job before or during prepare — an abort gate at the top of the
            function catches aborts landed since the pickup commit — in which case submission
            was skipped and the net released.

    Raises:
        Exception: If the FL backend is unsupported, the net endpoint cannot be resolved, client
            availability validation fails, or training fails to start. On failure the job is
            removed, the model is marked as errored, the net is released, and the original
            exception is re-raised.
    """
    try:
        logger.debug("Attempting to prepare and start training...")

        # Abort gate (#787): a user abort between the job pickup commit and this tick has
        # already DELETEd the job and released the net — take the clean aborted branch below
        # instead of surfacing the unpinned net as a "Failed to start training" false alarm.
        _raise_if_job_aborted(fl_job_id, session)

        # Resolve the backend from the net this job is pinned to. The value is the net's
        # canonical seeded backend (FLNets.fl_backend), read from the DB, never a boot-time env var.
        try:
            net_details = get_net_by_model_id(model_id, session)
        except NotFoundError:
            # The abort can land between the gate above and this lookup and unpin the net;
            # reclassify if so, otherwise a missing net is a genuine error.
            _raise_if_job_aborted(fl_job_id, session)
            raise
        if not net_details.endpoint:
            raise Exception("Failed to get the net endpoint")

        fl_backend = resolve_backend(session, net_details)

        # NOTE folder structure of the bundle will be different depending on whether it's a FLARE / Flower app
        if fl_backend == FLBackend.NVFLARE:
            # Copies base application + user-uploaded model files into a destination bucket on S3
            dest_bucket_s3_path = bundle_nvflare_application(model_id)
            logger.info(f"Bundled the app for [nvflare] to '{dest_bucket_s3_path}'.")

        elif fl_backend == FLBackend.FLOWER:
            # Copies base application + user-uploaded model files into a destination bucket on S3
            dest_bucket_s3_path = bundle_flower_application(model_id)
            logger.info(f"Bundled the app for [flower] to '{dest_bucket_s3_path}'.")

        else:
            error_msg = f"Unsupported FL backend: {fl_backend}"
            logger.error(error_msg)
            raise Exception(error_msg)

        # Resolve FL kit slot names at the FL boundary. The FL protocol (NVFlare/Flower)
        # identifies participants by the CN baked into the kit's cert at provisioning
        # time — that's the slot name, NOT the trust's friendly hub-side name. Using
        # Trust.name here would make validate_client_availability fail with
        # "Clients unavailable: <friendly name>" because the FL server never saw it.
        slot_names = list(
            session.exec(
                select(FLKitSlot.slot_name).where(col(FLKitSlot.assigned_to_trust_id).in_(trust_ids))
            ).all()
        )

        validate_client_availability(slot_names, net_details.endpoint, fl_backend)

        # Get presigned URLs from the files in the destination bucket on S3
        bundle_urls = get_bundle_urls(dest_bucket_s3_path)

        start_training(
            model_id=model_id,
            fl_job_id=fl_job_id,
            clients=slot_names,
            endpoint=net_details.endpoint,
            bundle_urls=bundle_urls,
            session=session,
        )

        add_log(model_id, f"Model training assigned to '{net_details.name}'", session)
        return True

    except JobAbortedError:
        # A user abort deleted the job while it was being prepared (#787): not a scheduler
        # error. The job is already DELETED and the model STOPPED — just make sure the net is
        # free (the abort usually released it already; this is an idempotent belt-and-braces).
        logger.info(f"Job {fl_job_id} for model {model_id} was aborted mid-prepare; skipping submission.")
        release_scheduler_for_model(model_id, session)
        return False

    except Exception as e:
        logger.error(f"Failed to start training: {e}")
        error_message = str(e)
        logger.info(f"Error message: {error_message}")
        remove_job(fl_job_id, session)
        add_log(model_id, error_message, session, success=False)
        update_model_status(model_id, ModelStatus.ERROR, session)
        # update_model_status(ERROR) can't free the net (its scheduler lookup only considers
        # non-DELETED jobs and remove_job just DELETEd this one), so release it explicitly.
        release_scheduler_for_model(model_id, session)

        logger.debug("Reverted job and released scheduler pickup")
        raise e


def get_required_training_details(model_id: UUID, session: Session) -> IRequiredTrainingInformation:
    """
    Fetches the necessary details for training a model, including the project ID and the latest cohort query.

    Args:
        model_id (UUID): The ID of the model to train.
        session (Session): The database session.

    Returns:
        IRequiredTrainingInformation: The required training information.

    Raises:
        flip_api.utils.exceptions.NotFoundError: If the model or its associated cohort query cannot be found.
        DatabaseError: If the query fails at the DB layer.
    """
    logger.info(f"Getting required details for training for model: {model_id}")

    try:
        # Fetch the model and associated project ID
        model = session.exec(select(Model).where(Model.id == model_id)).first()
        if not model:
            raise NotFoundError(f"Model with ID {model_id} not found")

        project_id = model.project_id

        # Fetch the latest query for the project
        latest_query = session.exec(
            select(Queries)
            .where(Queries.project_id == project_id)
            .order_by(cast(Column, Queries.created).desc())
            .limit(1)
        ).first()

        if not latest_query:
            raise NotFoundError("No cohort query found for this project")

        return IRequiredTrainingInformation(project_id=str(project_id), cohort_query=latest_query.query)

    except SQLAlchemyError as e:
        logger.error(f"Error getting required training details: {e}")
        raise DatabaseError("Error getting required training details") from e


def update_fl_scheduler(model_id: UUID, session: Session) -> None:
    """
    Updates the FL job status to COMPLETED and sets the associated scheduler status to AVAILABLE.

    Args:
        model_id (UUID): The ID of the model to update.
        session (Session): The database session.

    Returns:
        None

    Raises:
        DatabaseError: If the update fails at the DB layer.
    """
    try:
        logger.debug(f"Attempting to update the FL job to {JobStatus.COMPLETED} for model: {model_id}")

        # Get the latest non-deleted job for this model. Use created DESC so a
        # model that has been retried (multiple jobs) always releases the
        # scheduler pinned to the most recent job, not a stale earlier one.
        job_stmt = (
            select(FLJob)
            .where(FLJob.model_id == model_id, FLJob.status != JobStatus.DELETED)
            .order_by(cast(Column, FLJob.created).desc())
            .limit(1)
        )
        job = session.exec(job_stmt).first()
        if job:
            job.status = JobStatus.COMPLETED
            job.completed = datetime.utcnow()
            session.add(job)

            # Only query for the scheduler if job is present
            scheduler_stmt = select(FLScheduler).where(FLScheduler.job_id == job.id)
            scheduler = session.exec(scheduler_stmt).first()
            if scheduler:
                scheduler.status = NetStatus.AVAILABLE
                session.add(scheduler)

        session.commit()

        if job:
            # Completing a still-QUEUED job (e.g. a queued model stopped or
            # errored) re-ranks everything behind it; for a job already picked
            # up this normally emits nothing (emit-on-change), though it also
            # self-heals rows a previously failed emission rolled back.
            log_queue_positions(session)

        if job and scheduler:
            logger.info(
                "FL job %s set to COMPLETED, scheduler %s released to AVAILABLE.",
                job.id,
                scheduler.id,
            )
        elif job:
            logger.warning(
                "FL job %s set to COMPLETED, but no scheduler found for job_id=%s "
                "(scheduler may have already been reassigned).",
                job.id,
                job.id,
            )
        else:
            logger.info("No active FL job found for model; scheduler status unchanged.")

    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Error updating FL scheduler: {e}")
        raise DatabaseError("Error updating FL scheduler") from e
