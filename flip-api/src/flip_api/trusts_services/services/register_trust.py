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

"""Trust registration service — the single write path into the `trust` table.

Two callers:

- ``POST /admin/trusts`` (``trusts_services.admin_create_trust``) for one-off
  admin-driven registrations from the UI.
- ``flip_api.scripts.register_trust`` CLI, invoked once per trust by the deploy
  Makefile's ``register-trust`` target (the trust's name comes from its kit
  file; the hub keeps no trust list of its own).

Both produce the same on-disk state: one ``Trust`` row with ``api_key_hash``
set, one ``FLKitSlot`` assigned, plaintext api/internal-service keys returned
once for distribution to the trust host (then unrecoverable).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from flip_api.auth import trust_key_cache
from flip_api.config import get_settings
from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.db.seed.fl_kit_slots import insert_missing_slots, resolve_fl_kit_slot_names
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.scripts.generate_trust_key import generate_trust_key
from flip_api.trusts_services.utils.audit_helper import audit_trust_action
from flip_api.utils.logger import logger


class TrustRegistrationError(Exception):
    """Base for trust registration failures."""


class EmptyTrustNameError(TrustRegistrationError):
    """Caller supplied a blank trust name."""


class EmptyTrustCodeError(TrustRegistrationError):
    """Caller supplied a blank (or missing) trust code — code is required."""


class DuplicateTrustError(TrustRegistrationError):
    """A trust with the given name already exists."""


class NoFreeKitSlotError(TrustRegistrationError):
    """The FL kit slot pool is exhausted."""


class KitSlotSourceUnreadableError(TrustRegistrationError):
    """The slot source could not be read, so whether a slot is free is unknown."""


@dataclass(frozen=True)
class RegisteredTrust:
    """Result of a successful registration — the only place plaintext keys exist.

    Plaintext ``trust_api_key`` and ``trust_internal_service_key`` are returned
    exactly once: the hub stores only the api-key's SHA-256 hash, and the
    internal-service key is never persisted hub-side.
    """

    trust: Trust
    fl_kit_slot: FLKitSlot
    trust_api_key: str
    trust_internal_service_key: str


def _claim_free_slot(session: Session, slot_names: list[str]) -> FLKitSlot | None:
    """Lock and return the lowest-numbered unassigned FL kit slot among ``slot_names``, if any.

    SKIP LOCKED so two concurrent registrations never collide on the same row.

    Args:
        session (Session): The registration transaction's SQLModel session.
        slot_names (list[str]): The slots the slot source names now.

    Returns:
        FLKitSlot | None: The claimed (row-locked) slot, or ``None`` when none of
        ``slot_names`` is unassigned.
    """
    return session.exec(
        select(FLKitSlot)
        .where(col(FLKitSlot.assigned_to_trust_id).is_(None), col(FLKitSlot.slot_name).in_(slot_names))
        .order_by(col(FLKitSlot.slot_number).asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()


def register_trust(
    name: str,
    code: str | None,
    region: str | None,
    session: Session,
    audit_user_id: UUID | None = None,
) -> RegisteredTrust:
    """Atomically register a trust: mint keys, claim an FL kit slot, insert the row.

    Args:
        name (str): Friendly display name (any non-empty string after strip).
        code (str | None): Short code (e.g. ``GSTT``). Required — must be non-empty
            after strip. Names are arbitrary/non-unique, so the code is the stable
            short handle used in kit filenames and operator tooling.
        region (str | None): Optional NHS region.
        session (Session): SQLModel session; the function commits before returning.
        audit_user_id (UUID | None): Cognito sub of the authenticated admin from
            the UI path, or ``None`` for the deploy-CLI path (which runs under
            operator IAM, not a FLIP user). Stamped on the ``trusts_audit`` row
            written in the same transaction as the trust insert.

    Returns:
        RegisteredTrust: The persisted trust, its assigned FL kit slot, and the
        plaintext api / internal-service keys (returned once — discard from memory
        immediately after handing them to the operator).

    Raises:
        EmptyTrustNameError: ``name.strip()`` is empty.
        EmptyTrustCodeError: ``code`` is missing or empty after strip.
        DuplicateTrustError: A trust with this name already exists.
        NoFreeKitSlotError: None of the slots the slot source names is unassigned.
        KitSlotSourceUnreadableError: The slot source could not be read.
    """
    name = name.strip()
    if not name:
        raise EmptyTrustNameError("Trust name is required.")
    code = code.strip() if code else ""
    if not code:
        raise EmptyTrustCodeError("Trust code is required.")
    region = region.strip() if region else None

    if session.exec(select(Trust).where(Trust.name == name)).first() is not None:
        raise DuplicateTrustError(f"A trust named {name!r} already exists.")

    api_key, api_key_hash = generate_trust_key()
    # The internal-service key is per-trust and lives only on the trust side
    # (protects imaging-api / data-access-api from sibling containers). The hub
    # never validates it, so the hash is intentionally discarded — only the
    # plaintext is returned to the admin, once.
    internal_key, _ = generate_trust_key()

    trust = Trust(
        name=name,
        code=code,
        region=region,
        api_key_hash=api_key_hash,
        created_at=datetime.now(timezone.utc),
    )

    # The pool is what the slot source names right now (resolve_fl_kit_slot_names): a
    # slot added since boot gets its row here, and one withdrawn since is not claimed.
    slot_names = resolve_fl_kit_slot_names()
    if slot_names is None:
        # Not "no slots": free ones may exist, so do not send the admin off to mint kits.
        raise KitSlotSourceUnreadableError(
            "The FL kit-slot source could not be read. Try again; if it keeps failing, check the flip-api logs."
        )
    try:
        with session.begin_nested():
            inserted = insert_missing_slots(session, slot_names)
        if inserted:
            logger.info(f"FL kit slot pool grew at registration: added {inserted}")
    except IntegrityError as e:
        # A concurrent registration inserted the same slot rows between our
        # existence check and the savepoint flush (slot_name is the PK). Fine —
        # the rows exist either way; the claim below sees them. Only the
        # new FLKitSlot rows are pending inside this savepoint (the trust insert
        # comes later), so nothing else can be the flush that raised here.
        logger.info(f"Concurrent registration added the FL kit slot rows first ({e.orig!r})")
    # Claim the next free slot in the same transaction as the trust insert.
    # SKIP LOCKED so two concurrent registrations don't collide on the same row.
    slot = _claim_free_slot(session, slot_names)
    if slot is None:
        # The raised message is user-facing (UI snackbar / CLI); the operator
        # remediation lives here in the logs.
        logger.error(
            "No free FL kit slot among those the slot source names. Development: add slots to FL_KIT_SLOT_NAMES and "
            "restart flip-api. Stag/prod: `make -C deploy/providers/AWS add-fl-kits N=<n> HUB=<stag|prod>`. "
            "If kits already exist and still don't appear, check earlier logs for a kit left out as incomplete."
        )
        raise NoFreeKitSlotError("No FL kit slots available. Pre-provision more FL kits and try again.")

    session.add(trust)
    session.flush()  # populate trust.id before binding the slot
    slot.assigned_to_trust_id = trust.id
    slot.assigned_at = datetime.now(timezone.utc)

    # Write the audit row in the same transaction as the insert: if commit
    # rolls back (e.g. unique-name race), no orphan audit entry is left.
    audit_trust_action(
        trust_id=trust.id,
        trust_name=trust.name,
        action=TrustAuditAction.REGISTERED,
        user_id=audit_user_id,
        session=session,
    )

    session.commit()
    session.refresh(trust)
    session.refresh(slot)

    # Bust the in-process auth cache so the new trust authenticates immediately
    # in this worker. Cross-process eviction is bounded by the cache TTL.
    trust_key_cache.invalidate()

    return RegisteredTrust(
        trust=trust,
        fl_kit_slot=slot,
        trust_api_key=api_key,
        trust_internal_service_key=internal_key,
    )


def rotate_trust_keys(
    session: Session,
    *,
    name: str,
    revoke_old: bool = False,
    grace: timedelta | None = None,
) -> RegisteredTrust:
    """Reissue a trust's api and internal-service keys in place (FLIP#1569, FLIP#1714).

    The trust row, its id, its code and its FL kit slot assignment are unchanged, and the new
    plaintext keys are returned once for the operator to write into the trust's kit file. The
    change and a ROTATED audit row commit in one transaction.

    By default the rotation is **staged**: the new key's hash goes into ``pending_api_key_hash``
    and the hub accepts both keys, so the site stays online while its new kit is delivered. The
    first request the trust makes with the new key promotes it and the old key is refused from then
    on (``authenticate_trust``). If the new key is still unused after ``grace`` it is dropped and
    the old key carries on. Rotating again while a key is pending replaces the pending key, which is
    how a lost kit is recovered: the site never stopped working.

    ``revoke_old`` is the immediate cutover, for a key known to be leaked: the new hash replaces the
    live one at once, any pending key is dropped, and the trust is offline until its containers are
    recreated from the new kit.

    Args:
        session (Session): SQLModel session; the function commits before returning.
        name (str): Trust display name, as ``register_one_trust`` looks it up.
        revoke_old (bool): Refuse the old key from this commit on instead of staging the new one.
        grace (timedelta | None): How long a staged key may stay unused. Defaults to
            ``TRUST_KEY_ROTATION_GRACE_DAYS``.

    Returns:
        RegisteredTrust: The trust, its FL kit slot, and the new plaintext keys.

    Raises:
        TrustRegistrationError: No trust of that name is registered, or it has no FL kit slot.
    """
    trust = session.exec(select(Trust).where(Trust.name == name.strip())).first()
    if trust is None:
        raise TrustRegistrationError(f"No trust named {name!r} is registered.")

    slot = session.exec(select(FLKitSlot).where(FLKitSlot.assigned_to_trust_id == trust.id)).first()
    if slot is None:
        raise TrustRegistrationError(
            f"Trust {trust.name!r} has no FL kit slot assigned, so its kit cannot be reissued. "
            "Reclaiming a slot is a separate operation; do not rotate the api key without one."
        )

    api_key, api_key_hash = generate_trust_key()
    # Per-trust and trust-side only: the hub never validates this key, so its hash is discarded.
    internal_key, _ = generate_trust_key()

    expires_at: datetime | None = None
    if revoke_old:
        trust.api_key_hash = api_key_hash
        trust.pending_api_key_hash = None
        trust.pending_api_key_expires_at = None
    else:
        window = grace if grace is not None else timedelta(days=get_settings().TRUST_KEY_ROTATION_GRACE_DAYS)
        expires_at = datetime.now(timezone.utc) + window
        trust.pending_api_key_hash = api_key_hash
        trust.pending_api_key_expires_at = expires_at

    # Same transaction as the hash change: a rolled-back rotation leaves no audit claim behind.
    audit_trust_action(
        trust_id=trust.id,
        trust_name=trust.name,
        action=TrustAuditAction.ROTATED,
        user_id=None,
        session=session,
    )

    session.add(trust)
    session.commit()
    session.refresh(trust)

    # Drop this process's entry; serving workers re-check the live hash on every cache hit.
    trust_key_cache.invalidate()

    if revoke_old:
        logger.info("Rotated keys for trust %r (id=%s); the old key is refused from now on.", trust.name, trust.id)
    else:
        # Log the local expiry, not the model attribute: the timestamp is not sensitive, but the
        # attribute's name (`pending_api_key_...`) reads as a credential to the clear-text logging check.
        logger.info(
            "Staged new keys for trust %r (id=%s); both keys work until it first uses the new one, or until %s.",
            trust.name,
            trust.id,
            expires_at,
        )

    return RegisteredTrust(
        trust=trust,
        fl_kit_slot=slot,
        trust_api_key=api_key,
        trust_internal_service_key=internal_key,
    )
