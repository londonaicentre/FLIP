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

import re

from sqlmodel import Session, col, select

from flip_api.config import get_settings
from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.db.seed.seed_logger import logger
from flip_api.db.seed.trusts import SEED_NAME_OVERRIDES

_SLOT_NUMBER_RE = re.compile(r"_(\d+)$")


def _slot_number(slot_name: str) -> int:
    """Extract the trailing integer from a slot name (``Trust_007`` → ``7``).

    Slots are conventionally named with a trailing ``_<N>`` so the Flower side can pick
    a per-supernode key with the matching ``supernode_credentials_<N>`` suffix. Falls
    back to 0 for slot names without a trailing integer — those still work for NVFLARE
    (which only cares about the directory name) but break Flower's per-slot key lookup.
    """
    match = _SLOT_NUMBER_RE.search(slot_name)
    return int(match.group(1)) if match else 0


def seed_fl_kit_slots(session: Session) -> None:
    """Populate the ``fl_kit_slot`` pool from ``FL_KIT_SLOT_NAMES``.

    Inserts one row per configured slot name if not already present. Never deletes,
    re-assigns, or un-assigns rows — operators rely on the assignment table to be
    stable across restarts. For each seeded ``Trust`` whose name matches a slot
    name, backfills ``assigned_to_trust_id`` so dev fixtures (Trust_1/Trust_2/…)
    don't have to go through ``POST /admin/trusts`` to claim a slot.

    Args:
        session (Session): The SQLModel session used for reads and inserts.
    """
    slot_names: list[str] = get_settings().FL_KIT_SLOT_NAMES or []

    for slot_name in slot_names:
        existing = session.exec(
            select(FLKitSlot).where(FLKitSlot.slot_name == slot_name)
        ).first()
        if existing is None:
            session.add(
                FLKitSlot(slot_name=slot_name, slot_number=_slot_number(slot_name))
            )
    session.commit()

    # Backfill: a seeded trust gets bound to its matching slot if the slot's env-key
    # name matches the trust. The trust's *display* name may have been overridden via
    # SEED_NAME_OVERRIDES (e.g. "Trust_1" → "(Mock) GSTT"), so we resolve the override
    # before lookup. Idempotent — only writes when the slot is currently unassigned.
    if slot_names:
        unassigned = session.exec(
            select(FLKitSlot).where(
                col(FLKitSlot.slot_name).in_(slot_names),
                col(FLKitSlot.assigned_to_trust_id).is_(None),
            )
        ).all()
        for slot in unassigned:
            override = SEED_NAME_OVERRIDES.get(slot.slot_name)
            trust_display_name = override[0] if override else slot.slot_name
            trust = session.exec(
                select(Trust).where(Trust.name == trust_display_name)
            ).first()
            if trust is not None:
                slot.assigned_to_trust_id = trust.id
        session.commit()

    logger.info(f"Seeded fl_kit_slot pool ({len(slot_names)} slots configured).")
