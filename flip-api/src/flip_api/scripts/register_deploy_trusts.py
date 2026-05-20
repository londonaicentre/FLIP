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

"""Idempotently register every trust listed in ``settings.DEPLOY_TRUSTS``.

Invoked at deploy time (one-off ECS task in prod, ``docker compose exec`` in
dev). For each entry, if a ``Trust`` with that name already exists, skip it
silently — the host already has its kit and we can't recover the plaintext key
the second time around. Otherwise call ``register_trust`` and accumulate the
returned kit.

Contract:

- **stdout** (one line) — JSON array of kits for the newly-registered trusts.
  Empty list when nothing was registered. Parsed by the deploy distributor.
- **stderr** — human-readable logging.
- Exit code 0 when every input entry either succeeded or was skipped; 1 if any
  entry failed mid-batch (the successful ones are still on stdout so kits are
  never lost).
"""

import json
import sys
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from flip_api.config import get_settings
from flip_api.db.database import engine
from flip_api.db.models.main_models import Trust
from flip_api.trusts_services.services.register_trust import (
    TrustRegistrationError,
    register_trust,
)
from flip_api.utils.logger import logger


class DeployTrust(BaseModel):
    """One entry of ``DEPLOY_TRUSTS`` — what the deploy wants the hub to know about."""

    name: str = Field(min_length=1)
    code: str | None = None
    region: str | None = None


def _kit_to_dict(name: str, kit: Any) -> dict[str, Any]:
    """Shape one new registration into the kit dict the deploy distributor expects."""
    return {
        "trust_id": str(kit.trust.id),
        "trust_name": kit.trust.name,
        "trust_api_key": kit.trust_api_key,
        "trust_internal_service_key": kit.trust_internal_service_key,
        "fl_kit_slot": kit.fl_kit_slot.slot_name,
        "fl_kit_slot_number": kit.fl_kit_slot.slot_number,
    }


def register_deploy_trusts(session: Session, entries: list[DeployTrust]) -> tuple[list[dict[str, Any]], int]:
    """Register each entry idempotently. Returns ``(kits, failures)``.

    Args:
        session (Session): SQLModel session.
        entries (list[DeployTrust]): Validated entries from ``settings.DEPLOY_TRUSTS``.

    Returns:
        tuple[list[dict[str, Any]], int]: ``(new_kits, failure_count)`` — kits for
        newly-registered trusts; ``failure_count`` counts entries that errored.
    """
    new_kits: list[dict[str, Any]] = []
    failures = 0
    for entry in entries:
        if session.exec(select(Trust).where(Trust.name == entry.name)).first() is not None:
            logger.info("Trust %r already registered — skipping.", entry.name)
            continue
        try:
            kit = register_trust(
                name=entry.name, code=entry.code, region=entry.region, session=session
            )
        except TrustRegistrationError as e:
            logger.error("Failed to register trust %r: %s", entry.name, e)
            session.rollback()
            failures += 1
            continue
        logger.info("Registered trust %r (id=%s) with kit slot %r.",
                    kit.trust.name, kit.trust.id, kit.fl_kit_slot.slot_name)
        new_kits.append(_kit_to_dict(entry.name, kit))
    return new_kits, failures


def main() -> None:
    """CLI entry point: read settings.DEPLOY_TRUSTS, register, emit kits to stdout."""
    raw_entries = get_settings().DEPLOY_TRUSTS or []
    try:
        entries = [DeployTrust.model_validate(item) for item in raw_entries]
    except Exception as e:
        logger.error("Invalid DEPLOY_TRUSTS entry: %s", e)
        # Empty kit list on stdout so a parsing deploy script doesn't choke.
        print("[]")
        sys.exit(1)

    if not entries:
        logger.info("DEPLOY_TRUSTS is empty — nothing to register.")
        print("[]")
        return

    with Session(engine) as session:
        new_kits, failures = register_deploy_trusts(session, entries)

    # Single JSON line on stdout — the deploy distributor reads this and writes
    # per-trust kit files to the trust hosts. Logging goes to stderr.
    print(json.dumps(new_kits))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
