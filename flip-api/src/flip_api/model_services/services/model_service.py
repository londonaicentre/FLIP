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

from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from flip_api.db.models.main_models import FLLogs, FLMetrics, Model, ModelTrustIntersect, Trust
from flip_api.domain.interfaces.model import (
    IDetailedModelStatus,
    IModelAuditAction,
    IModelDetails,
    IModelMetrics,
    IModelMetricsData,
    IModelMetricsValue,
)
from flip_api.domain.schemas.actions import ModelAuditAction
from flip_api.domain.schemas.status import ModelStatus
from flip_api.fl_services.services import fl_scheduler_service
from flip_api.model_services.utils.audit_helper import audit_model_action, audit_model_actions
from flip_api.utils.logger import logger


def edit_model(model_id: UUID, model_details: IModelDetails, user_id: UUID, session: Session) -> None:
    """
    Edit the model details

    Args:
        model_id (UUID): The ID of the model to be edited
        model_details (IModelDetails): The new details for the model
        user_id (UUID): The ID of the user making the changes
        session (Session): The database session
    """
    logger.debug("Attempting to update model details...")

    model = session.get(Model, model_id)
    if not model:
        raise ValueError(f"Model {model_id} not found")

    model.name = model_details.name
    model.description = model_details.description

    session.add(model)
    session.commit()

    logger.info("Model details updated")

    audit_response = audit_model_action(model_id, ModelAuditAction.EDIT, user_id, session)
    logger.info(f"Output: {audit_response}")


def update_model_status(model_id: UUID, status: ModelStatus | None, session: Session) -> ModelStatus | None:
    """
    Update the status of a model

    Args:
        model_id (UUID): The ID of the model
        status (ModelStatus | None): The new status to be set
        session (Session): The database session

    Returns:
        ModelStatus | None: The updated status of the model, or None if the model does not exist.
    """
    logger.info(f"Attempting to set the model's status... ID: {model_id}, Status: {status}")

    model = session.get(Model, model_id)
    if not model:
        logger.warning("Model not found. No update performed.")
        return None

    if not status:
        status = model.status

    model.status = status
    session.commit()

    logger.info(f"The status of Model ID: {model_id} has been updated to {status}")

    if status in [ModelStatus.ERROR, ModelStatus.STOPPED, ModelStatus.RESULTS_UPLOADED]:
        fl_scheduler_service.update_fl_scheduler(model_id, session)

    return status


def add_log(
    model_id: UUID, log: str, session: Session, transaction: Any | None = None, success: bool = True
) -> None:
    """
    Add a log entry to the database

    Args:
        model_id (UUID): The ID of the model.
        log (str): The log message to be added.
        session (Session): The database session.
        transaction (Any | None): Optional transaction to control commit behavior.
        success (bool): Indicates if the log entry is a success or failure.

    Returns:
        None
    """
    logger.info({"message": "Attempting to add a log line for model...", "modelId": model_id, "log": log})

    # Create the log entry
    log_entry = FLLogs(model_id=model_id, log=log, success=success)

    try:
        session.add(log_entry)

        if transaction is None:
            session.commit()

        logger.info(f"A log has been added for model {model_id}.")

    except Exception as e:
        logger.error(f"Failed to add log for model {model_id}: {str(e)}")
        session.rollback()
        raise

    return None


def delete_model(model_id: UUID, user_id: UUID, session: Session) -> None:
    """
    Delete a model by setting its 'deleted' attribute to True.

    Args:
        model_id (UUID): The ID of the model to be deleted.
        user_id (UUID): The ID of the user performing the deletion.
        session (Session): The database session.

    Raises:
        ValueError: If the model with the given ID does not exist.
    """
    logger.debug("Attempting to delete model...")

    model = session.get(Model, model_id)
    if not model:
        raise ValueError(f"Model {model_id} not found")

    model.deleted = True

    # Audit the deletion action
    audit_model_action(model_id, ModelAuditAction.DELETE, user_id, session)

    session.commit()

    logger.info(f"Model {model_id} soft-deleted and audit log recorded.")


def delete_models(project_id: UUID, user_id: str, session: Session, ensure_deletion: bool = True) -> int:
    """
    Delete all models associated with a given project by setting their 'deleted' attribute to True.

    Args:
        project_id (UUID): The ID of the project whose models are to be deleted.
        user_id (str): The ID of the user performing the deletion.
        session (Session): The database session.
        ensure_deletion (bool): If True, raises an error if no models are found for deletion.

    Returns:
        int: The number of models deleted.
    """
    logger.debug("Attempting to delete models...")

    models = session.exec(select(Model).where(Model.project_id == project_id, not Model.deleted)).all()

    if ensure_deletion and not models:
        raise ValueError(f"Failed to delete models for project: {project_id}")

    model_ids = [m.id for m in models]
    for model in models:
        model.deleted = True

    if model_ids:
        audits = [
            IModelAuditAction(model_id=model_id, action=ModelAuditAction.DELETE, userid=user_id)
            for model_id in model_ids
        ]
        audit_response = audit_model_actions(audits, session)
        logger.info(f"Output: {audit_response}")
    else:
        logger.warning("No models deleted")
        return 0

    session.commit()

    logger.info(f"Models for project {project_id} soft-deleted and audit logs recorded.")

    return len(model_ids)


def get_model_status(model_id: UUID, session: Session) -> IDetailedModelStatus | None:
    """
    Get the status of a model.

    Args:
        model_id (UUID): The ID of the model.
        session (Session): The database session.

    Returns:
        IDetailedModelStatus | None: The model's status and deleted flag, or None if the model does not exist.
    """
    logger.debug("Attempting to get the model's status...")

    model = session.get(Model, model_id)
    if not model:
        return None

    return IDetailedModelStatus(status=model.status, deleted=model.deleted)


def validate_trusts(model_id: UUID, trusts: list[str], session: Session) -> bool:
    """
    Validate whether the trusts are associated with the model.

    Args:
        model_id (UUID): The ID of the model.
        trusts (list[str]): A list of trust names to validate.
        session (Session): The database session.

    Returns:
        bool: True if all trusts are associated with the model, False otherwise.
    """
    logger.debug(f"Attempting to validate whether the trusts: {trusts} are associated with the model: {model_id} ...")

    # Get all trusts associated with the model
    stmt = (
        select(Trust.name)
        .join(ModelTrustIntersect, Trust.id == ModelTrustIntersect.trust_id)  # type: ignore[arg-type]
        .where(ModelTrustIntersect.model_id == model_id)
    )

    result = session.exec(stmt).all()
    associated_trusts = set(result)

    logger.debug(f"Trusts associated with model {model_id}: {associated_trusts}")

    # Check if all input trusts are in the associated trusts
    missing_trusts = set(trusts) - associated_trusts
    if missing_trusts:
        logger.debug(f"Trusts not associated with model {model_id}: {missing_trusts}")
        logger.error(f"One or more trusts in: {trusts} are not an approved trust(s)")
        return False

    return True


def get_metrics(model_id: UUID, session: Session) -> list[IModelMetrics]:
    """
    Get the metrics for a given model.

    Args:
        model_id (UUID): The ID of the model.
        session (Session): The database session.

    Returns:
        list[IModelMetrics]: A list of metrics associated with the model.
    """
    logger.debug("Attempting to retrieve the metrics results for the model...")

    results = session.exec(select(FLMetrics).where(FLMetrics.model_id == model_id)).all()

    if not results:
        logger.warning("No metrics have been found")
        return []

    # metrics_map: label -> IModelMetrics
    metrics_map: dict[str, IModelMetrics] = {}

    for row in results:
        # Create or get the IModelMetrics for the label
        if row.label not in metrics_map:
            metrics_map[row.label] = IModelMetrics(yLabel=row.label, xLabel="globalRound", metrics=[])

        # Get the list of series for this label
        metric = metrics_map[row.label]

        # Find or create the series for this trust
        series = next((s for s in metric.metrics if s.seriesLabel == row.trust), None)
        if not series:
            series = IModelMetricsData(seriesLabel=row.trust, data=[])
            metric.metrics.append(series)

        # Add the data point
        series.data.append(IModelMetricsValue(xValue=row.global_round, yValue=row.result))

    return list(metrics_map.values())
