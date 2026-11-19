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
from collections import defaultdict

from sqlmodel import Session, col, select

from flip_api.config import get_settings
from flip_api.db.models.main_models import FLKitSlot
from flip_api.db.seed.seed_logger import logger
from flip_api.domain.schemas.types import FLBackend
from flip_api.utils.s3_client import S3Client

# A FLARE client kit is complete on a net once these startup files are there (FLIP#1720).
# add_fl_kits.sh uploads them last, so a half-uploaded kit never qualifies; the hub's own
# server and admin kits have no fed_client.json, so they never do either.
FLARE_KIT_REQUIRED_FILES = frozenset({"client.crt", "client.key", "rootCA.pem", "fed_client.json", "sub_start.sh"})
# What marks a kit as the hub's own (server, admin): expected to fall short, so not warned about.
_HUB_KIT_FILES = frozenset({"fed_server.json", "fed_admin.json"})
_KIT_KEY_RE = re.compile(r"(net-\d+)/(?:services/([^/]+)/(?:startup/([^/]+)$)?)?")

# A Flower slot is complete on a net once both halves of its SuperNode key pair are there
# (FLIP#1942). add_fl_kits_flower.sh uploads the private key last, after the SuperLink has
# registered the public one, so a slot is never claimable before its SuperNode is admitted.
# Slot Trust_<N> is keys/supernode_credentials_<N>{,.pub}: the name
# register-supernode-keys.sh gives the same key at the SuperLink.
_FLOWER_KEY_RE = re.compile(r"(net-\d+)/(?:keys/(supernode_credentials_(\d+)(?:\.pub)?)$)?")

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


def resolve_fl_kit_slot_names() -> list[str] | None:
    """Resolve the FL kit-slot names from their environment's single source.

    - Development: the ``FL_KIT_SLOT_NAMES`` env var (a ``DevSettings``-only field).
    - Production, NVFLARE: the kits themselves — every slot with a complete kit on every
      net under ``FLARE_KITS_S3_URI``.
    - Production, Flower: the SuperNode keys themselves — every slot with a complete key
      pair on every net under ``FLOWER_KITS_S3_URI``.

    register_trust reads this at every registration and claims only from the names it
    returns: a slot added since boot is claimable with no restart, and one withdrawn
    since is never handed out.

    Deliberately no fallback: an unreadable source returns ``None``, logged, and no slot
    can be claimed until it is fixed. Rows and assignments already in the pool are
    untouched.

    Returns:
        list[str] | None: The slot names, empty when none are configured; ``None`` when the
        source could not be read (an AWS error).
    """
    settings = get_settings()
    if settings.ENV != "production":
        # getattr: the field exists only on DevSettings, and mypy sees the
        # DevSettings | ProdSettings union here.
        return getattr(settings, "FL_KIT_SLOT_NAMES", []) or []
    if settings.FL_BACKEND == FLBackend.NVFLARE:
        return _complete_flare_kit_names(getattr(settings, "FLARE_KITS_S3_URI", ""))
    return _complete_flower_key_names(getattr(settings, "FLOWER_KITS_S3_URI", ""))


def _complete_flare_kit_names(kits_uri: str) -> list[str] | None:
    """List the slots whose FLARE kit is complete on every net under ``kits_uri``.

    A trust needs a working kit for every net, so a kit incomplete or missing on any net
    is left out, with a warning naming what is missing.

    Args:
        kits_uri (str): ``s3://<bucket>/fl-flare-participant-kits/<FLARE_KIT_DATE>``.

    Returns:
        list[str] | None: The complete slot names, empty when there are none; ``None`` when
        the listing fails.
    """
    if not kits_uri:
        logger.error("FLARE_KITS_S3_URI is not set. No kit slot can be claimed until it is.")
        return []
    prefix = f"{kits_uri.rstrip('/')}/"
    try:
        paths = S3Client().list_objects(prefix)
    except Exception:
        logger.exception(f"Could not list the FLARE kits under '{prefix}'. No kit slot can be claimed until fixed.")
        return None

    nets: set[str] = set()
    kits: defaultdict[str, dict[str, set[str]]] = defaultdict(dict)  # slot -> net -> startup file names
    for path in paths:
        if match := _KIT_KEY_RE.match(path.removeprefix(prefix)):
            net, slot_name, file_name = match.groups()
            nets.add(net)
            if slot_name:
                files = kits[slot_name].setdefault(net, set())
                if file_name:
                    files.add(file_name)

    if not nets:
        logger.error(f"No FLARE kits under '{prefix}' — is FLARE_KIT_DATE right? No kit slot can be claimed.")
        return []
    complete = []
    for slot, files_by_net in kits.items():
        missing = {net: FLARE_KIT_REQUIRED_FILES - files_by_net.get(net, set()) for net in sorted(nets)}
        if not any(missing.values()):
            complete.append(slot)
        elif not any(files & _HUB_KIT_FILES for files in files_by_net.values()):
            # Not one of the hub's own kits, so a client kit: say why it is left out.
            gaps = "; ".join(f"{net} lacks {', '.join(sorted(files))}" for net, files in missing.items() if files)
            logger.warning(f"FLARE kit '{slot}' is not claimable: {gaps}.")
    return sorted(complete, key=lambda name: (_slot_number(name), name))


def _complete_flower_key_names(kits_uri: str) -> list[str] | None:
    """List the slots whose SuperNode key pair is complete on every net under ``kits_uri``.

    A trust needs a working key for every net, so a pair incomplete or missing on any net
    is left out, with a warning naming what is missing.

    Args:
        kits_uri (str): ``s3://<bucket>/fl-flower-participant-kits/<FLOWER_KIT_DATE>``.

    Returns:
        list[str] | None: The complete slot names, empty when there are none; ``None`` when
        the listing fails.
    """
    if not kits_uri:
        logger.error("FLOWER_KITS_S3_URI is not set. No kit slot can be claimed until it is.")
        return []
    prefix = f"{kits_uri.rstrip('/')}/"
    try:
        paths = S3Client().list_objects(prefix)
    except Exception:
        logger.exception(f"Could not list the Flower keys under '{prefix}'. No kit slot can be claimed until fixed.")
        return None

    nets: set[str] = set()
    keys: defaultdict[int, dict[str, set[str]]] = defaultdict(dict)  # slot number -> net -> key file names
    for path in paths:
        if match := _FLOWER_KEY_RE.match(path.removeprefix(prefix)):
            net, file_name, number = match.groups()
            nets.add(net)
            if file_name:
                keys[int(number)].setdefault(net, set()).add(file_name)

    if not nets:
        logger.error(f"No Flower keys under '{prefix}' — is FLOWER_KIT_DATE right? No kit slot can be claimed.")
        return []
    complete = []
    for number, files_by_net in sorted(keys.items()):
        slot = f"Trust_{number}"
        required = {f"supernode_credentials_{number}", f"supernode_credentials_{number}.pub"}
        missing = {net: required - files_by_net.get(net, set()) for net in sorted(nets)}
        if not any(missing.values()):
            complete.append(slot)
        else:
            gaps = "; ".join(f"{net} lacks {', '.join(sorted(files))}" for net, files in missing.items() if files)
            logger.warning(f"Flower key '{slot}' is not claimable: {gaps}.")
    return complete


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
    if not slot_names:
        return []
    existing = set(session.exec(select(FLKitSlot.slot_name).where(col(FLKitSlot.slot_name).in_(slot_names))).all())
    inserted = [slot_name for slot_name in dict.fromkeys(slot_names) if slot_name not in existing]
    for slot_name in inserted:
        session.add(FLKitSlot(slot_name=slot_name, slot_number=_slot_number(slot_name)))
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
    slot_names = resolve_fl_kit_slot_names() or []  # an unreadable source seeds nothing; it is logged
    insert_missing_slots(session, slot_names)
    session.commit()

    logger.info(f"Seeded fl_kit_slot pool ({len(slot_names)} slots configured).")
