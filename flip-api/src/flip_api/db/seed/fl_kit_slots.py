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
from collections import defaultdict

from botocore.exceptions import BotoCoreError, ClientError
from sqlmodel import Session, select

from flip_api.config import get_settings
from flip_api.db.models.main_models import FLKitSlot
from flip_api.db.seed.seed_logger import logger
from flip_api.domain.schemas.types import FLBackend
from flip_api.utils.get_parameters import get_parameter
from flip_api.utils.s3_client import S3Client

# Flower's slot list (Terraform-rendered, see deploy/providers/AWS/parameter_store.tf):
# the SuperLink labels SuperNodes from the same list at startup, so it stays authored
# config there. Slot names are plain configuration, not secrets, hence Parameter Store.
_FL_KIT_SLOT_NAMES_PARAMETER = "/flip/fl_kit_slot_names"

# A FLARE client kit is complete on a net once these startup files are there (FLIP#1720).
# add_fl_kits.sh uploads them last, so a half-uploaded kit never qualifies; the hub's own
# server and admin kits have no fed_client.json, so they never do either.
FLARE_KIT_REQUIRED_FILES = frozenset({"client.crt", "client.key", "rootCA.pem", "fed_client.json", "sub_start.sh"})
_KIT_KEY_RE = re.compile(r"(net-\d+)/(?:services/([^/]+)/startup/([^/]+)$)?")

_SLOT_NUMBER_RE = re.compile(r"_(\d+)$")

# Sentinel used as slot_number for names without a trailing integer (e.g. "Trust_K8s").
# Must be larger than any real slot number so the free-slot query (ORDER BY slot_number ASC)
# never hands these out before conventional Trust_N slots.  Flower's per-supernode key
# lookup requires a trailing _<N> suffix; non-numeric names still work for FLARE but
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
    """Resolve the FL kit-slot names from their environment's single source.

    - Development: the ``FL_KIT_SLOT_NAMES`` env var (a ``DevSettings``-only field).
    - Production, NVFLARE: the kits themselves — every slot with a complete kit on every
      net under ``FLARE_KITS_S3_URI``. A kit minted by ``make add-fl-kits`` is claimable
      at once; register_trust re-reads this when the pool runs dry, so no restart.
    - Production, Flower: the ``/flip/fl_kit_slot_names`` SSM parameter.

    Deliberately no fallback: an unreadable source returns an empty list, logged. The
    boot seed and the registration reconcile are additive, so existing pool rows and
    assignments are untouched; the pool simply cannot grow until the source is fixed.

    Returns:
        list[str]: The slot names; empty when none are configured or the source is unreadable.
    """
    settings = get_settings()
    if settings.ENV != "production":
        # getattr: the field exists only on DevSettings, and mypy sees the
        # DevSettings | ProdSettings union here.
        return getattr(settings, "FL_KIT_SLOT_NAMES", []) or []
    if settings.FL_BACKEND == FLBackend.NVFLARE:
        return _complete_flare_kit_names(getattr(settings, "FLARE_KITS_S3_URI", ""))
    return _slot_names_from_parameter()


def _complete_flare_kit_names(kits_uri: str) -> list[str]:
    """List the slots whose FLARE kit is complete on every net under ``kits_uri``.

    A trust needs a working kit for every net, so a kit incomplete or missing on any net
    is left out. That only keeps a slot from being added: a row already in the pool stays.

    Args:
        kits_uri (str): ``s3://<bucket>/fl-flare-participant-kits/<FLARE_KIT_DATE>``.

    Returns:
        list[str]: The complete slot names; empty when there are none or the listing fails.
    """
    if not kits_uri:
        logger.error("FLARE_KITS_S3_URI is not set. The kit-slot pool cannot grow.")
        return []
    prefix = f"{kits_uri.rstrip('/')}/"
    try:
        paths = S3Client().list_objects(prefix)
    except Exception:
        logger.exception(f"Could not list the FLARE kits under '{prefix}'. The kit-slot pool cannot grow.")
        return []

    nets: set[str] = set()
    startup_files: defaultdict[tuple[str, str], set[str]] = defaultdict(set)  # (slot, net) -> file names
    for path in paths:
        if match := _KIT_KEY_RE.match(path.removeprefix(prefix)):
            net, slot_name, file_name = match.groups()
            nets.add(net)
            if slot_name:
                startup_files[slot_name, net].add(file_name)

    if not nets:
        logger.error(f"No FLARE kits under '{prefix}' — is FLARE_KIT_DATE right? The kit-slot pool cannot grow.")
        return []
    complete = {
        slot
        for slot, _ in startup_files
        if all(FLARE_KIT_REQUIRED_FILES <= startup_files.get((slot, net), set()) for net in nets)
    }
    return sorted(complete, key=lambda name: (_slot_number(name), name))


def _slot_names_from_parameter() -> list[str]:
    """Read Flower's slot list from the ``/flip/fl_kit_slot_names`` SSM parameter.

    A missing parameter is a not-yet-provisioned source (WARNING); any other AWS error or
    a malformed value means the source is broken (ERROR).

    Returns:
        list[str]: The configured slot names; empty when the parameter is unreadable.
    """
    try:
        slot_names = json.loads(get_parameter(_FL_KIT_SLOT_NAMES_PARAMETER))
        if not isinstance(slot_names, list) or not all(isinstance(name, str) for name in slot_names):
            raise ValueError(f"expected a JSON list of strings, got: {slot_names!r}"[:200])
        return slot_names
    except (BotoCoreError, ClientError, TypeError, ValueError) as e:
        if isinstance(e, ClientError) and e.response.get("Error", {}).get("Code") == "ParameterNotFound":
            logger.warning(
                f"SSM parameter '{_FL_KIT_SLOT_NAMES_PARAMETER}' does not exist yet — a Terraform apply creates it. "
                "The kit-slot pool cannot grow until then (existing slots and assignments are unaffected)."
            )
            return []
        logger.error(
            f"Could not read SSM parameter '{_FL_KIT_SLOT_NAMES_PARAMETER}' ({e!r}). The kit-slot pool "
            "cannot grow until this is fixed (existing slots and assignments are unaffected)."
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
        existing = session.exec(select(FLKitSlot).where(FLKitSlot.slot_name == slot_name)).first()
        if existing is None:
            session.add(FLKitSlot(slot_name=slot_name, slot_number=_slot_number(slot_name)))
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
