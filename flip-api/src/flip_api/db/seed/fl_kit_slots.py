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

import json
import re

from botocore.exceptions import ClientError
from sqlmodel import Session, select

from flip_api.config import get_settings
from flip_api.db.models.main_models import FLKitSlot
from flip_api.db.seed.seed_logger import logger
from flip_api.utils.get_secrets import get_secret

# Key inside the FLIP_API Secrets Manager secret holding the JSON list of slot names.
# The secret is the runtime source of truth in production so operators can grow the
# pool with a targeted `terraform apply` of the secret — no task-definition revision,
# no flip-api restart (register_trust reconciles on a pool miss).
_FL_KIT_SLOT_NAMES_SECRET_KEY = "fl_kit_slot_names"

_SLOT_NUMBER_RE = re.compile(r"_(\d+)$")

# Sentinel used as slot_number for names without a trailing integer (e.g. "Trust_K8s").
# Must be larger than any real slot number so the free-slot query (ORDER BY slot_number ASC)
# never hands these out before conventional Trust_N slots.  Flower's per-supernode key
# lookup requires a trailing _<N> suffix; non-numeric names still work for NVFLARE but
# should be reserved for purpose-built slots (K8s, staging) that are registered explicitly.
_NON_NUMERIC_SLOT_NUMBER = 10_000


def _slot_number(slot_name: str) -> int:
    """Extract the trailing integer from a slot name (``Trust_007`` → ``7``).

    Slots are conventionally named with a trailing ``_<N>`` so the Flower side can pick
    a per-supernode key with the matching ``supernode_credentials_<N>`` suffix. Returns
    ``_NON_NUMERIC_SLOT_NUMBER`` for names without a trailing integer so they sort after
    all conventional ``Trust_N`` slots in the free-slot claim queue.
    """
    match = _SLOT_NUMBER_RE.search(slot_name)
    return int(match.group(1)) if match else _NON_NUMERIC_SLOT_NUMBER


def resolve_fl_kit_slot_names() -> list[str]:
    """Resolve the configured FL kit-slot names from their environment-appropriate source.

    In production the source of truth is the ``fl_kit_slot_names`` key of the FLIP_API
    Secrets Manager secret (a JSON list string, rendered by Terraform from the env
    file's ``FL_KIT_SLOT_NAMES``), so the pool can grow via a targeted apply of the
    secret without a task-definition change or restart. In development the
    ``FL_KIT_SLOT_NAMES`` env var is used directly.

    Any failure to fetch or parse the secret (key not yet present, AWS error,
    malformed JSON, non-list payload) falls back to the env-var setting with a
    warning — this keeps rollout order-safe: the code can ship before the secret
    gains the key, and the task-definition env value still applies.

    Returns:
        list[str]: The configured slot names; empty when none are configured.
    """
    if get_settings().ENV != "production":
        return get_settings().FL_KIT_SLOT_NAMES or []

    try:
        slot_names = json.loads(get_secret(_FL_KIT_SLOT_NAMES_SECRET_KEY))
        if not isinstance(slot_names, list) or not all(isinstance(name, str) for name in slot_names):
            raise ValueError(f"expected a JSON list of strings, got {type(slot_names).__name__}")
        return slot_names
    except (KeyError, ClientError, ValueError) as e:
        logger.warning(
            f"Could not read '{_FL_KIT_SLOT_NAMES_SECRET_KEY}' from the FLIP_API secret ({e}); "
            "falling back to the FL_KIT_SLOT_NAMES environment value."
        )
        return get_settings().FL_KIT_SLOT_NAMES or []


def insert_missing_slots(session: Session, slot_names: list[str]) -> list[str]:
    """Insert a row for each slot name not already in the ``fl_kit_slot`` pool.

    Additive only — never deletes, re-assigns, or un-assigns rows; operators rely on
    the assignment table to be stable. Does not commit: the caller owns the
    transaction (boot seeding commits; register_trust's reconcile shares the
    registration transaction).

    Args:
        session (Session): The SQLModel session used for reads and inserts.
        slot_names (list[str]): The configured slot names to ensure exist.

    Returns:
        list[str]: The slot names that were newly inserted.
    """
    inserted: list[str] = []
    for slot_name in slot_names:
        existing = session.exec(
            select(FLKitSlot).where(FLKitSlot.slot_name == slot_name)
        ).first()
        if existing is None:
            session.add(
                FLKitSlot(slot_name=slot_name, slot_number=_slot_number(slot_name))
            )
            inserted.append(slot_name)
    return inserted


def seed_fl_kit_slots(session: Session) -> None:
    """Populate the ``fl_kit_slot`` pool from the resolved slot names.

    Inserts one row per configured slot name if not already present. Never deletes,
    re-assigns, or un-assigns rows — operators rely on the assignment table to be
    stable across restarts. ``register_trust`` claims a free slot atomically when
    a trust is registered, so the seed has no slot→trust binding to do.

    Args:
        session (Session): The SQLModel session used for reads and inserts.
    """
    slot_names = resolve_fl_kit_slot_names()
    insert_missing_slots(session, slot_names)
    session.commit()

    logger.info(f"Seeded fl_kit_slot pool ({len(slot_names)} slots configured).")
