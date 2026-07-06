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

"""Hard-delete a single trust on the hub (deploy-time CLI).

Removes the trust row entirely so the slot can be UI-re-registered cleanly
(name free, FL kit slot free, no archived row left behind). Handles the
trust.id FK landscape explicitly — the schema has nine tables that reference
trust.id and none of them declare ON DELETE CASCADE, so a naive
``session.delete(trust)`` would fail with a foreign-key violation. This
script clears each dependent table in the right order before deleting the
trust row.

Dependent tables (from flip_api.db.models.main_models):

| Table                    | FK field                  | Strategy        |
|--------------------------|---------------------------|-----------------|
| fl_kit_slot              | assigned_to_trust_id      | NULL the FK     |
| fl_job_trust             | trust_id (PK part)        | DELETE rows     |
| fl_metrics               | trust (NOT NULL)          | DELETE rows     |
| fl_logs                  | trust (nullable)          | DELETE rows     |
| model_trust_intersect    | trust_id (nullable)       | DELETE rows     |
| project_trust_intersect  | trust_id (nullable)       | DELETE rows     |
| query_result             | trust_id (nullable)       | DELETE rows     |
| trust_task               | trust_id (NOT NULL)       | DELETE rows     |
| xnat_project_status      | trust_id (nullable)       | DELETE rows     |

For the nullable FKs we could either NULL or DELETE — DELETE makes the
intent clearer (a metric/log/result tied to a now-gone trust serves no
purpose and would orphan stats in the UI). The fl_kit_slot row is the
exception: NULL'ing reclaims the slot for future registrations, which is
the whole point of the delete.

Contract:

- **stdout** — a single JSON object:

  - ``status: "deleted"`` — hard-delete succeeded. Includes ``trust_id``,
    ``name``, ``freed_fl_kit_slot``, and a ``dependent_rows_deleted`` dict
    summarising the cascade.
  - ``status: "not_found"`` — no trust with that name; nothing to do.
    Includes ``name``.

- **stderr** — human-readable logging.
- Exit code 0 on either status; non-zero only on argument errors or
  unexpected exceptions (e.g. an FK we haven't accounted for).

Invoked by ``deploy/providers/AWS/scripts/delete-trust.sh`` as a one-off
ECS task (mirrors the ``register-trusts.sh`` pattern). For local dev:
``docker compose exec flip-api uv run python -m flip_api.scripts.delete_trust --name <slot>``.
"""

import argparse
import json
from typing import Any

from sqlmodel import Session, delete, select

from flip_api.auth import trust_key_cache
from flip_api.db.database import get_engine
from flip_api.db.models.main_models import (
    FLJobTrust,
    FLKitSlot,
    FLLogs,
    FLMetrics,
    ModelTrustIntersect,
    ProjectTrustIntersect,
    QueryResult,
    Trust,
    TrustTask,
    XNATProjectStatus,
)
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.trusts_services.utils.audit_helper import audit_trust_action
from flip_api.utils.logger import logger

# (model class, FK field name) for each table that references trust.id.
# fl_kit_slot is handled separately — we NULL the FK rather than delete.
_DEPENDENT_TABLES: tuple[tuple[type, str], ...] = (
    (FLJobTrust, "trust_id"),
    (FLMetrics, "trust"),
    (FLLogs, "trust"),
    (ModelTrustIntersect, "trust_id"),
    (ProjectTrustIntersect, "trust_id"),
    (QueryResult, "trust_id"),
    (TrustTask, "trust_id"),
    (XNATProjectStatus, "trust_id"),
)


def delete_one_trust(name: str, session: Session) -> dict[str, Any]:
    """Hard-delete a trust by name. Cascades through dependent tables.

    Args:
        name (str): Trust display name (matches ``trust.name`` exactly).
        session (Session): SQLModel session.

    Returns:
        dict[str, Any]: ``{"status": "deleted", ...}`` on success or
        ``{"status": "not_found", "name": name}`` when there's nothing
        to delete.
    """
    name = name.strip()
    trust = session.exec(select(Trust).where(Trust.name == name)).first()
    if trust is None:
        logger.info("Trust %r not found — nothing to delete.", name)
        return {"status": "not_found", "name": name}

    trust_id = trust.id
    trust_name = trust.name

    # NULL the FL kit slot first so the row can be reclaimed by a future
    # registration. Done before the dependent-rows pass so a partial run
    # doesn't leave the slot stuck assigned to a half-deleted trust.
    freed_slot: str | None = None
    slot = session.exec(
        select(FLKitSlot).where(FLKitSlot.assigned_to_trust_id == trust_id)
    ).first()
    if slot is not None:
        slot.assigned_to_trust_id = None
        slot.assigned_at = None
        session.add(slot)
        freed_slot = slot.slot_name

    # Delete dependent rows table-by-table. SQLAlchemy's bulk `delete()`
    # is one statement per table — faster than a row-by-row session.delete()
    # and avoids loading the rows into memory. Routed through
    # session.execute() (not SQLModel's exec(), which only accepts SELECTs).
    deleted_counts: dict[str, int] = {}
    for model, fk_field in _DEPENDENT_TABLES:
        result = session.execute(
            delete(model).where(getattr(model, fk_field) == trust_id)
        )
        # __tablename__ is set on every SQLModel table class above; cast
        # via getattr so mypy doesn't trip over the loosely-typed `type`
        # element in _DEPENDENT_TABLES. rowcount lives on CursorResult,
        # which session.execute returns for DML.
        table_name: str = getattr(model, "__tablename__")
        deleted_counts[table_name] = result.rowcount  # type: ignore[attr-defined]

    # Write the audit row in the same transaction as the delete so a partial
    # cascade failure rolls back the audit too. `audit_user_id` is None — the
    # deploy CLI runs under bastion IAM, not an authenticated FLIP user. The
    # operator identity lives in CloudTrail (ecs:RunTask issuer), not here.
    audit_trust_action(
        trust_id=trust_id,
        trust_name=trust_name,
        action=TrustAuditAction.DELETED,
        user_id=None,
        session=session,
    )

    session.delete(trust)
    session.commit()

    # Bust the in-process auth cache so a stale entry can't keep the deleted
    # trust authenticating in this worker. Cross-process eviction is bounded
    # by the TTL; the verify-against-live-row step in authenticate_trust
    # closes the residual window safely (db.get returns None → fall-through →
    # 401). The CLI runs in its own process so this call is a no-op locally,
    # but the same service function is also called from the admin endpoint
    # path where invalidation matters.
    trust_key_cache.invalidate()

    logger.info(
        "Hard-deleted trust %r (id=%s) — freed FL kit slot %r, dependent rows: %s.",
        trust_name,
        trust_id,
        freed_slot,
        deleted_counts,
    )
    return {
        "status": "deleted",
        "trust_id": str(trust_id),
        "name": trust_name,
        "freed_fl_kit_slot": freed_slot,
        "dependent_rows_deleted": deleted_counts,
    }


def main() -> None:
    """CLI entry point: hard-delete one trust, emit its status JSON to stdout."""
    parser = argparse.ArgumentParser(description="Hard-delete one trust on the hub.")
    parser.add_argument("--name", required=True, help="Trust name (exact match).")
    args = parser.parse_args()

    with Session(get_engine()) as session:
        result = delete_one_trust(args.name, session)

    # Single JSON line on stdout — the deploy script reads this to confirm
    # outcome. Logging went to stderr.
    print(json.dumps(result))


if __name__ == "__main__":
    main()
