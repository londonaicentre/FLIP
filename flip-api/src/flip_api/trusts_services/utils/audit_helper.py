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

from flip_api.db.models.main_models import TrustsAudit
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.utils.logger import logger


def audit_trust_action(
    trust_id: UUID,
    trust_name: str,
    action: TrustAuditAction,
    user_id: UUID | None,
    session: Session,
) -> TrustsAudit:
    """Insert an audit log row for a trust-registry mutation.

    Mirrors the project / model audit helpers in pattern — the caller is
    responsible for the surrounding transaction commit.

    Args:
        trust_id (UUID): ID of the trust being audited. Stored as a bare
            UUID (no FK) so the row survives hard-delete of the trust.
        trust_name (str): Human-readable name at the time of the action.
            Denormalised here so audit queries remain meaningful after
            hard-delete.
        action (TrustAuditAction): The action performed.
        user_id (UUID | None): Cognito sub of the authenticated admin
            (UI path), or ``None`` when called from the deploy CLI.
        session (Session): SQLModel session — the helper does NOT commit.

    Returns:
        TrustsAudit: The newly-flushed audit row.

    Raises:
        ValueError: If the audit row cannot be persisted.
    """
    logger.debug("Auditing trust action %s for trust %s", action.value, trust_id)
    row = TrustsAudit(
        trust_id=trust_id,
        trust_name=trust_name,
        action=action,
        modified_by_user_id=user_id,
    )
    session.add(row)
    session.flush()

    if not row.id:
        raise ValueError("Unable to create audit for trust")

    return row
