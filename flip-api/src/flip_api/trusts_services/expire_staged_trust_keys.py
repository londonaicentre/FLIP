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

"""Drop staged trust keys whose deadline passed unused (FLIP#1714).

A staged key stops authenticating at its deadline (``authenticate_trust`` checks it on every
request), so this job is only cleanup: it clears the pending columns and writes the KEY_EXPIRED
audit row. It runs on the scheduler rather than in the auth path so that a failed write can never
turn a trust's request into a 500.
"""

from datetime import datetime, timezone

from sqlalchemy import or_, update
from sqlmodel import Session, col

from flip_api.db.database import get_engine
from flip_api.db.models.main_models import Trust
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.trusts_services.utils.audit_helper import audit_trust_action
from flip_api.utils.logger import logger


def expire_staged_trust_keys(db: Session, now: datetime | None = None) -> int:
    """Clear every staged key past its deadline, auditing each, in one transaction.

    One conditional ``UPDATE ... RETURNING``, so the rows changed are exactly those still expired at
    write time: a key re-staged or promoted since is left alone. A staged key without a deadline
    counts as expired, as it does for auth.

    Args:
        db (Session): Database session; committed before returning.
        now (datetime | None): The moment to expire against. Defaults to the current time.

    Returns:
        int: How many staged keys were dropped.
    """
    cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    expired = db.execute(
        update(Trust)
        .where(
            col(Trust.pending_api_key_hash).is_not(None),
            or_(col(Trust.pending_api_key_expires_at).is_(None), col(Trust.pending_api_key_expires_at) <= cutoff),
        )
        .values(pending_api_key_hash=None, pending_api_key_expires_at=None)
        .returning(col(Trust.id), col(Trust.name))
    ).all()
    for trust_id, trust_name in expired:
        audit_trust_action(
            trust_id=trust_id,
            trust_name=trust_name,
            action=TrustAuditAction.KEY_EXPIRED,
            user_id=None,
            session=db,
        )
        logger.warning(
            "Trust %r never used its rotated key before the deadline; it was dropped and the old key still works.",
            trust_name,
        )
    db.commit()
    return len(expired)


def expire_staged_trust_keys_scheduled_task() -> None:
    """Scheduler entry point: run ``expire_staged_trust_keys``, logging rather than raising on failure."""
    try:
        with Session(get_engine()) as db:
            expire_staged_trust_keys(db)
    except Exception:
        logger.exception("Error in scheduled expiry of staged trust keys")
