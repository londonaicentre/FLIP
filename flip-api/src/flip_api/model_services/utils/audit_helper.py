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

from uuid import UUID

from sqlmodel import Session

from flip_api.db.models.main_models import ModelsAudit
from flip_api.domain.interfaces.model import IModelAuditAction
from flip_api.domain.schemas.actions import ModelAuditAction
from flip_api.utils.logger import logger


def audit_model_action(model_id: UUID, action: ModelAuditAction, user_id: UUID, session: Session) -> ModelsAudit:
    """
    Insert a single audit log into the ModelsAudit table.

    Args:
        model_id (UUID): The ID of the model being audited.
        action (ModelAuditAction): The action performed on the model.
        user_id (UUID): The ID of the user performing the action.
        session (Session): SQLModel session.

    Returns:
        ModelsAudit: The created ModelsAudit entry.
    """
    logger.debug("Attempting to audit the action...")

    audit = ModelsAudit(
        model_id=model_id,
        action=action,
        user_id=user_id,
    )

    session.add(audit)
    session.flush()  # Ensures audit gets an ID

    return audit


def audit_model_actions(actions: list[IModelAuditAction], session: Session) -> list[ModelsAudit]:
    """
    Bulk insert multiple audit logs into the ModelsAudit table.

    Args:
        actions (list[IModelAuditAction]): List of actions to audit.
        session (Session): SQLModel session.

    Returns:
        list[ModelsAudit]: List of created ModelsAudit entries.

    Raises:
        RuntimeError: If ``actions`` is non-empty but no audit rows ended up being created.
    """
    logger.debug("Attempting to audit multiple actions...")

    audit_entries = [
        ModelsAudit(model_id=action.model_id, action=action.action, user_id=action.userid) for action in actions
    ]

    session.add_all(audit_entries)
    session.commit()

    for entry in audit_entries:
        session.refresh(entry)

    if not audit_entries:
        ids = ", ".join(str(a.model_id) for a in actions)
        raise RuntimeError(f"Unable to create audit for models: {ids}")

    return audit_entries
