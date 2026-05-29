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

from flip_api.db.models.main_models import FLKitSlot, FLLogs, FLMetrics, Model, ModelTrustIntersect, Trust
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

    Raises:
        ValueError: If no model exists with the given ``model_id``.
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


_STATUS_AUDIT_MAP: dict[ModelStatus, ModelAuditAction] = {
    ModelStatus.PREPARED: ModelAuditAction.PREPARED,
    ModelStatus.TRAINING_STARTED: ModelAuditAction.TRAINING_STARTED,
    ModelStatus.RESULTS_UPLOADED: ModelAuditAction.RESULTS_UPLOADED,
}


def update_model_status(
    model_id: UUID,
    status: ModelStatus | None,
    session: Session,
    user_id: UUID | None = None,
) -> ModelStatus | None:
    """
    Update the status of a model.

    Args:
        model_id (UUID): The ID of the model.
        status (ModelStatus | None): The new status to be set.
        session (Session): The database session.
        user_id (UUID | None): Optional user who triggered the change; recorded on
            the audit row when present. Background transitions pass None.

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

    previous_status = model.status
    model.status = status
    session.commit()

    logger.info(f"The status of Model ID: {model_id} has been updated to {status}")

    audit_action = _STATUS_AUDIT_MAP.get(status)
    if audit_action and previous_status != status:
        audit_model_action(model_id, audit_action, user_id, session)
        session.commit()

    if status in [ModelStatus.ERROR, ModelStatus.STOPPED, ModelStatus.RESULTS_UPLOADED]:
        fl_scheduler_service.update_fl_scheduler(model_id, session)

    return status


def add_log(
    model_id: UUID,
    log: str,
    session: Session,
    transaction: Any | None = None,
    success: bool = True,
    trust: Trust | None = None,
    fl_client_name: str | None = None,
) -> None:
    """
    Add a log entry to the database

    Args:
        model_id (UUID): The ID of the model.
        log (str): The log message to be added.
        session (Session): The database session.
        transaction (Any | None): Optional transaction to control commit behavior.
        success (bool): Indicates if the log entry is a success or failure.
        trust (Trust | None): The trust the log is attributed to, when the log was
            reported by an FL client. None for model-level (hub) logs.
        fl_client_name (str | None): The FL client name as reported by the FL server.
            None for model-level logs.

    Returns:
        None

    Raises:
        Exception: Re-raises any error encountered when persisting the log entry, after rolling
            back the session.
    """
    logger.info({"message": "Attempting to add a log line for model...", "modelId": model_id, "log": log})

    # Create the log entry
    log_entry = FLLogs(
        model_id=model_id,
        log=log,
        success=success,
        trust=trust.id if trust is not None else None,
        fl_client_name=fl_client_name,
    )

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

    Raises:
        ValueError: If ``ensure_deletion`` is True and no non-deleted models exist for the given
            project.
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


def resolve_trust_from_fl_client_name(fl_client_name: str, session: Session) -> Trust | None:
    """Resolve an FL client name (its FL kit slot) to its Trust.

    Both backends report the FL kit slot as the client name, so resolution is
    uniform — the slot is mapped to its assigned trust via the FLKitSlot table,
    independent of the operator-chosen trust display name (closes the #538 drift
    from Flower's previous free-form name lookup):

    - NVFLARE: the slot is the certificate CN baked in at provisioning time
      (e.g. ``Trust_2``).
    - Flower: the slot is the ``SUPERNODE_NAME`` env var, set to ``FL_KIT_SLOT``
      in the trust compose so the FL identity is the slot, not the trust name.

    Args:
        fl_client_name (str): The FL kit slot name reported by the FL server.
        session (Session): The database session.

    Returns:
        Trust | None: The resolved trust, or None when the slot name matches no
            assigned slot (an unknown / unassigned FL kit slot).
    """
    logger.debug(f"Resolving FL kit slot '{fl_client_name}' to its assigned trust ...")

    stmt = (
        select(Trust)
        .join(FLKitSlot, FLKitSlot.assigned_to_trust_id == Trust.id)  # type: ignore[arg-type]
        .where(FLKitSlot.slot_name == fl_client_name)
    )

    return session.exec(stmt).first()


def validate_trust_ids(model_id: UUID, trust_ids: list[UUID], session: Session) -> bool:
    """Validate that every trust id is associated with the model via ModelTrustIntersect.

    Args:
        model_id (UUID): The ID of the model.
        trust_ids (list[UUID]): Trust ids to validate.
        session (Session): The database session.

    Returns:
        bool: True if every id is associated with the model, False otherwise.
    """
    associated = set(
        session.exec(
            select(ModelTrustIntersect.trust_id).where(ModelTrustIntersect.model_id == model_id)
        ).all()
    )
    missing = set(trust_ids) - associated
    if missing:
        logger.error(f"Trust ids not approved for model {model_id}: {missing}")
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

    # FLMetrics.trust is the assigned trust's id. Resolve it to the trust's short
    # `code` (e.g. "UCLH") for chart legends, falling back to the trust's name.
    trust_label: dict[UUID, str] = {}
    for trust_id, code, name in session.exec(select(Trust.id, Trust.code, Trust.name)).all():
        trust_label[trust_id] = code or name

    # metrics_map: label -> IModelMetrics
    metrics_map: dict[str, IModelMetrics] = {}

    for row in results:
        # Create or get the IModelMetrics for the label
        if row.label not in metrics_map:
            metrics_map[row.label] = IModelMetrics(yLabel=row.label, xLabel="globalRound", metrics=[])

        # Get the list of series for this label
        metric = metrics_map[row.label]

        series_label = trust_label.get(row.trust, str(row.trust))

        # Find or create the series for this trust
        series = next((s for s in metric.metrics if s.seriesLabel == series_label), None)
        if not series:
            series = IModelMetricsData(seriesLabel=series_label, data=[])
            metric.metrics.append(series)

        # Add the data point
        series.data.append(IModelMetricsValue(xValue=row.global_round, yValue=row.result))

    return list(metrics_map.values())
