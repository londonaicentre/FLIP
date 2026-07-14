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
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from flip_api.auth import trust_key_cache
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


def _claim_free_slot(session: Session) -> FLKitSlot | None:
    """Lock and return the lowest-numbered unassigned FL kit slot, if any.

    SKIP LOCKED so two concurrent registrations never collide on the same row.

    Args:
        session (Session): The registration transaction's SQLModel session.

    Returns:
        FLKitSlot | None: The claimed (row-locked) slot, or ``None`` when the pool
        has no unassigned rows.
    """
    return session.exec(
        select(FLKitSlot)
        .where(col(FLKitSlot.assigned_to_trust_id).is_(None))
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
        NoFreeKitSlotError: The ``fl_kit_slot`` pool has no unassigned rows, even
            after the on-miss reconcile from the configured slot names.
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

    # Claim the next free FL kit slot in the same transaction as the insert.
    # SKIP LOCKED so two concurrent registrations don't collide on the same row.
    slot = _claim_free_slot(session)
    if slot is None:
        # Pool exhausted — but the configured slot list may have grown since boot
        # (in production the /flip/fl_kit_slot_names SSM parameter is the live source;
        # see resolve_fl_kit_slot_names). Reconcile additively and retry the claim once.
        try:
            with session.begin_nested():
                inserted = insert_missing_slots(session, resolve_fl_kit_slot_names())
            if inserted:
                logger.info(f"FL kit slot pool reconciled at registration: added {inserted}")
        except IntegrityError as e:
            # A concurrent registration inserted the same slot rows between our
            # existence check and the savepoint flush (slot_name is the PK). Fine —
            # the rows exist either way; the retried claim below sees them. Only the
            # new FLKitSlot rows are pending inside this savepoint (the trust insert
            # comes later), so nothing else can be the flush that raised here.
            logger.info(f"Concurrent registration reconciled the FL kit slot pool first ({e.orig!r}); retrying claim")
        slot = _claim_free_slot(session)
    if slot is None:
        # The raised message is user-facing (UI snackbar / CLI); the operator
        # remediation lives here in the logs.
        logger.error(
            "FL kit slot pool exhausted even after reconcile. Development: add slots to FL_KIT_SLOT_NAMES and "
            "restart flip-api. Stag/prod: append the new slot names to FL_KIT_SLOT_NAMES in the env file and run "
            "`make -C deploy/providers/AWS apply-fl-kit-slots` (or grow the pool end-to-end with "
            "`make -C deploy/providers/AWS add-fl-kits`). If slots were already applied and still don't appear, "
            "check earlier logs for /flip/fl_kit_slot_names SSM read errors."
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
