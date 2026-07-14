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

from botocore.exceptions import BotoCoreError, ClientError
from sqlmodel import Session, select

from flip_api.config import get_settings
from flip_api.db.models.main_models import FLKitSlot
from flip_api.db.seed.seed_logger import logger
from flip_api.utils.get_parameters import get_parameter

# SSM parameter holding the JSON list of slot names (Terraform-managed, see
# deploy/providers/AWS/parameter_store.tf). The parameter is the runtime source of
# truth in production so operators can grow the pool with a targeted `terraform
# apply` of just this parameter — no task-definition revision, no flip-api restart
# (register_trust reconciles on a pool miss). Slot names are plain configuration,
# not secrets, hence Parameter Store per the /flip/* convention.
_FL_KIT_SLOT_NAMES_PARAMETER = "/flip/fl_kit_slot_names"

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
    """Resolve the configured FL kit-slot names from their environment's single source.

    In production the source is the ``/flip/fl_kit_slot_names`` SSM parameter (a JSON
    list string, rendered by Terraform from the env file's ``FL_KIT_SLOT_NAMES``), so
    the pool can grow via a targeted apply of just that parameter without a
    task-definition change or restart. In development the source is the
    ``FL_KIT_SLOT_NAMES`` env var (a ``DevSettings``-only field).

    Deliberately no prod fallback: a failure to fetch or parse the parameter returns
    an empty list — the boot seed and the registration reconcile are additive, so
    existing pool rows and assignments are untouched; the pool simply cannot grow
    until the parameter is fixed, and nothing stale masks that. The log level splits
    the two situations: a missing parameter is a not-yet-provisioned source (WARNING);
    any other AWS error or a malformed value means the source of truth is broken (ERROR).

    Note the pre-activation state is ERROR, not WARNING: ``make apply-fl-kit-slots``
    creates the parameter *and* grants the task role's ``ssm:GetParameter``, so an
    image deployed before that apply gets ``AccessDeniedException`` (ERROR) rather than
    ``ParameterNotFound`` — observed on stag 2026-07-14. Either way the pool is intact
    and the log names the fix.

    Returns:
        list[str]: The configured slot names; empty when none are configured or the
        parameter is unreadable.
    """
    if get_settings().ENV != "production":
        # getattr: the field exists only on DevSettings, and mypy sees the
        # DevSettings | ProdSettings union here.
        return getattr(get_settings(), "FL_KIT_SLOT_NAMES", []) or []

    try:
        slot_names = json.loads(get_parameter(_FL_KIT_SLOT_NAMES_PARAMETER))
        if not isinstance(slot_names, list) or not all(isinstance(name, str) for name in slot_names):
            raise ValueError(f"expected a JSON list of strings, got: {slot_names!r}"[:200])
        return slot_names
    except (BotoCoreError, ClientError, TypeError, ValueError) as e:
        if isinstance(e, ClientError) and e.response.get("Error", {}).get("Code") == "ParameterNotFound":
            logger.warning(
                f"SSM parameter '{_FL_KIT_SLOT_NAMES_PARAMETER}' does not exist yet — run "
                "`make -C deploy/providers/AWS apply-fl-kit-slots` to create it. The kit-slot pool "
                "cannot grow until then (existing slots and assignments are unaffected)."
            )
            return []
        logger.error(
            f"Could not read SSM parameter '{_FL_KIT_SLOT_NAMES_PARAMETER}' ({e!r}). The kit-slot pool "
            "cannot grow until this is fixed — new slots applied with `make apply-fl-kit-slots` will NOT "
            "become claimable (existing slots and assignments are unaffected)."
        )
        return []


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
