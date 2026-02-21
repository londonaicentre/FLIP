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
from typing import List, Optional, cast
from uuid import UUID

from sqlalchemy import Column
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from flip_api.db.models.main_models import (
    FLJob,
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
from flip_api.fl_services.services.fl_service import (
    bundle_application,
    get_bundle_urls,
    start_training,
    validate_client_availability,
)
from flip_api.model_services.services.model_service import add_log, update_model_status, validate_trusts
from flip_api.utils.exceptions import DatabaseError, NotFoundError
from flip_api.utils.logger import logger


def remove_job(job_id: UUID, session: Session):
    """
    Sets the job status to DELETED and clears the started timestamp.

    Args:
        job_id (UUID): The ID of the job to remove.
        session (Session): SQLModel session.

    Returns:
        None
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


def remove_job_from_queue(model_id: UUID, session: Session):
    """
    Sets the job status to DELETED for all jobs associated with the given model ID.

    Args:
        model_id (UUID): The model ID whose jobs are to be removed.
        session (Session): SQLModel session.

    Returns:
        None
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

    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Error removing job from queue: {e}")
        raise DatabaseError("Error removing job from queue") from e


def revert_scheduler_pickup(scheduler_id: UUID, session: Session):
    """
    Sets the scheduler status to AVAILABLE and clears the job_id.

    Args:
        scheduler_id (UUID): The ID of the scheduler to revert.
        session (Session): SQLModel session.

    Returns:
        None
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


def get_net_by_model_id(model_id: UUID, session: Session) -> INetDetails:
    """
    Get information for a net by model ID.

    Args:
        model_id (UUID): The model ID.
        session (Session): SQLModel session.

    Returns:
        INetDetails: Details of the net.
    """
    logger.info("Getting the net endpoint via its model ID...")
    try:
        statement = (
            select(FLNets.endpoint, FLNets.name)
            .join(FLScheduler)
            .join(FLJob)
            .where(FLJob.model_id == model_id)
            .limit(1)
        )
        result = session.exec(statement).first()

        if not result or not result[0]:
            logger.error(f"Net not found for model ID: {model_id}")
            raise NotFoundError(f"Net not found for model ID: {model_id}")

        endpoint, name = result
        return INetDetails(endpoint=endpoint, name=name)

    except SQLAlchemyError as e:
        logger.error(f"Error getting net by model ID: {e}")
        raise DatabaseError("Error getting net by model ID") from e


def get_net_by_name(name: str, session: Session) -> Optional[INetDetails]:
    """
    Get information for a net by name

    Args:
        name (str): Name of the net
        session (Session): SQLModel session

    Returns:
        Optional[INetDetails]: Details of the net or None if not found
    """
    logger.info(f"Getting {name} info from db...")

    try:
        statement = select(FLNets.endpoint, FLNets.name).where(FLNets.name == name)
        result = session.exec(statement).first()

        logger.info(f"Query result: {result}")

        if not result:
            logger.error(f"{name} could not be found")
            return None

        endpoint, net_name = result
        return INetDetails(endpoint=endpoint, name=net_name)

    except SQLAlchemyError as e:
        logger.error(f"Database error while getting net by name: {e}")
        raise DatabaseError("Database error while getting net by name") from e


def get_nets(session: Session) -> List[INetDetails]:
    """
    Fetches all nets from the database.

    Args:
        session (Session): The database session.

    Returns:
        List[INetDetails]: A list of all nets.
    """
    logger.info("Getting net info from db...")
    try:
        statement = select(FLNets.endpoint, FLNets.name)
        results = session.exec(statement).all()

        if not results:
            error_message = "No db response returned when querying for nets"
            logger.error(error_message)
            raise NotFoundError(error_message)

        return [INetDetails(endpoint=endpoint, name=name) for endpoint, name in results]

    except SQLAlchemyError as e:
        logger.error(f"Error getting nets: {e}")
        raise DatabaseError("Error getting nets") from e


def check_for_available_net(session: Session) -> Optional[ISchedulerResponse]:
    """
    Checks for any available nets and marks one as busy if found.

    Args:
        session (Session): The database session.

    Returns:
        Optional[ISchedulerResponse]: The scheduler response if an available net is found, otherwise None.
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


def check_for_queued_jobs(scheduler_id: UUID, session: Session) -> Optional[IJobResponse]:
    """
    Checks for any queued jobs for a given scheduler.

    Args:
        scheduler_id (UUID): The ID of the scheduler to check.
        session (Session): The database session.

    Returns:
        Optional[IJobResponse]: The job response if a queued job is found, otherwise None.
    """
    logger.info("Checking for any queued jobs...")

    try:
        # Find the earliest queued job
        job_stmt = (
            select(FLJob)
            .where(FLJob.status == JobStatus.QUEUED)
            .order_by(cast(Column, FLJob.created).asc())
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

        # Validate trusts
        if not validate_trusts(job.model_id, job.clients, session):
            raise Exception(f"[{', '.join(job.clients)}] contains invalid trusts")

        # Assign job to scheduler
        scheduler = session.get(FLScheduler, scheduler_id)
        if not scheduler:
            logger.error(f"Scheduler with id {scheduler_id} not found")
            raise NotFoundError(f"Scheduler with id {scheduler_id} not found")

        scheduler.job_id = job.id

        session.commit()

        return IJobResponse(id=job.id, model_id=job.model_id, clients=job.clients)

    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Error checking for queued jobs: {e}")
        revert_scheduler_pickup(scheduler_id, session)
        raise DatabaseError("Error checking for queued jobs") from e


def prepare_and_start_training(model_id: UUID, fl_job_id: UUID, clients: list[str], request_id: str, session: Session):
    """
    Prepares and starts the training process for a given model.

    Args:
        model_id (UUID): The ID of the model to train.
        fl_job_id (UUID): The ID of the federated learning job.
        clients (list[str]): The list of client IDs participating in the training.
        request_id (str): The ID of the request.
        session (Session): The database session.

    Returns:
        None
    """
    try:
        logger.debug("Attempting to prepare and start training...")

        # Copies base application + user-uploaded model files into a destination bucket on S3
        job_type = bundle_application(model_id)
        logger.info(f"Bundled the application for '{model_id}' and job_type '{job_type}'.")

        net_details = get_net_by_model_id(model_id, session)
        if not net_details.endpoint:
            raise Exception("Failed to get the net endpoint")

        validate_client_availability(clients, net_details.endpoint, request_id)

        # Get presigned URLs from the files in the destination bucket on S3
        bundle_urls = get_bundle_urls(model_id)

        start_training(model_id, fl_job_id, clients, net_details.endpoint, bundle_urls, request_id, session, job_type)

        add_log(model_id, f"Model training assigned to '{net_details.name}'", session)

    except Exception as e:
        logger.error(f"Failed to start training: {e}")
        error_message = str(e)
        logger.info(f"Error message: {error_message}")
        remove_job(fl_job_id, session)
        add_log(model_id, error_message, session, False)
        update_model_status(model_id, ModelStatus.ERROR, session)

        logger.debug("Reverted job and scheduler pickup")
        raise e


def get_required_training_details(model_id: UUID, session: Session) -> IRequiredTrainingInformation:
    """
    Fetches the necessary details for training a model, including the project ID and the latest cohort query.

    Args:
        model_id (UUID): The ID of the model to train.
        session (Session): The database session.

    Returns:
        IRequiredTrainingInformation: The required training information.
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


def update_fl_scheduler(model_id: UUID, session: Session):
    """
    Updates the FL job status to COMPLETED and sets the associated scheduler status to AVAILABLE.

    Args:
        model_id (UUID): The ID of the model to update.
        session (Session): The database session.

    Returns:
        None
    """
    try:
        logger.debug(f"Attempting to update the FL job to {JobStatus.COMPLETED} for model: {model_id}")

        # Get and update the job
        job_stmt = select(FLJob).where(FLJob.model_id == model_id)
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
        logger.info("The FL job status and FL scheduler status have been updated successfully.")

    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Error updating FL scheduler: {e}")
        raise DatabaseError("Error updating FL scheduler") from e
