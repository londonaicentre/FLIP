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

"""Soft-delete a single trust on the hub (deploy-time CLI).

Designed for the "I want to re-register this trust via the UI Add-Trust
flow" case — frees the trust's name and its FL kit slot without cascading
through the trust's history (cohort queries, projects, trust_task rows,
the 6+ tables that hold trust.id FKs). Hard-deleting on a prod hub where
the trust has been operational would either need cascade-delete configured
on every FK or a fragile per-table cleanup pass; soft-delete via
``disabled_at`` keeps the historical data intact while still freeing the
slot for re-registration.

Mechanics:

1. Stamp ``trust.disabled_at = now()``
2. Rename ``trust.name = <original>_archived_<unix_ts>`` so the original
   name is free for ``register_trust``'s existence check (which uses an
   exact name match, no disabled_at filter).
3. Null out ``fl_kit_slot.assigned_to_trust_id`` + ``assigned_at`` for
   whichever slot the trust held, so the next ``POST /admin/trusts`` can
   claim it.

Contract:

- **stdout** — a single JSON object:

  - ``status: "disabled"`` — soft-delete succeeded. Includes ``trust_id``,
    ``original_name``, ``archived_name``, ``freed_fl_kit_slot``.
  - ``status: "not_found"`` — no trust with that name; nothing to do.
    Includes ``name``.
  - ``status: "already_disabled"`` — trust was already soft-deleted by a
    prior run. Includes ``trust_id``, ``name``, ``disabled_at``.

- **stderr** — human-readable logging.
- Exit code 0 on any of the three statuses (all are non-error outcomes —
  the caller can decide what to do based on the JSON status). Non-zero
  only on argument errors or unexpected exceptions.

Invoked by ``deploy/providers/AWS/scripts/delete-trust.sh`` as a one-off
ECS task (mirrors the ``register-trusts.sh`` pattern). For local dev:
``docker compose exec flip-api uv run python -m flip_api.scripts.delete_trust --name <slot>``.
"""

import argparse
import json
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from flip_api.db.database import engine
from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.utils.logger import logger


def delete_one_trust(name: str, session: Session) -> dict[str, Any]:
    """Soft-delete a trust by name.

    Args:
        name (str): Trust display name (matches ``trust.name`` exactly).
        session (Session): SQLModel session.

    Returns:
        dict[str, Any]: One of three shapes — see module docstring.
    """
    name = name.strip()
    trust = session.exec(select(Trust).where(Trust.name == name)).first()
    if trust is None:
        logger.info("Trust %r not found — nothing to delete.", name)
        return {"status": "not_found", "name": name}

    if trust.disabled_at is not None:
        logger.info(
            "Trust %r (id=%s) already disabled at %s — no-op.",
            trust.name,
            trust.id,
            trust.disabled_at.isoformat(),
        )
        return {
            "status": "already_disabled",
            "trust_id": str(trust.id),
            "name": trust.name,
            "disabled_at": trust.disabled_at.isoformat(),
        }

    # Free the FL kit slot so the next registration can claim it.
    slot = session.exec(
        select(FLKitSlot).where(FLKitSlot.assigned_to_trust_id == trust.id)
    ).first()
    freed_slot: str | None = None
    if slot is not None:
        slot.assigned_to_trust_id = None
        slot.assigned_at = None
        session.add(slot)
        freed_slot = slot.slot_name

    # Stamp + rename so the original name is free for re-registration.
    # register_trust.py's existence check uses an exact-name match without a
    # disabled_at filter; without the rename a re-registration would treat
    # the disabled row as the existing trust and refuse to mint a new kit.
    now = datetime.now(timezone.utc)
    archived_name = f"{trust.name}_archived_{int(now.timestamp())}"
    original_name = trust.name
    trust.disabled_at = now
    trust.name = archived_name
    session.add(trust)
    session.commit()

    logger.info(
        "Soft-deleted trust %r (id=%s) — archived as %r, freed FL kit slot %r.",
        original_name,
        trust.id,
        archived_name,
        freed_slot,
    )
    return {
        "status": "disabled",
        "trust_id": str(trust.id),
        "original_name": original_name,
        "archived_name": archived_name,
        "freed_fl_kit_slot": freed_slot,
    }


def main() -> None:
    """CLI entry point: soft-delete one trust, emit its status JSON to stdout."""
    parser = argparse.ArgumentParser(description="Soft-delete one trust on the hub.")
    parser.add_argument("--name", required=True, help="Trust name (exact match).")
    args = parser.parse_args()

    with Session(engine) as session:
        result = delete_one_trust(args.name, session)

    # Single JSON line on stdout — the deploy script reads this to confirm
    # outcome. Logging went to stderr.
    print(json.dumps(result))


if __name__ == "__main__":
    main()
