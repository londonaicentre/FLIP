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
- ``flip_api.scripts.register_deploy_trusts`` CLI, invoked by the deploy
  Makefile to seed the trusts listed in ``settings.DEPLOY_TRUSTS``.

Both produce the same on-disk state: one ``Trust`` row with ``api_key_hash``
set, one ``FLKitSlot`` assigned, plaintext api/internal-service keys returned
once for distribution to the trust host (then unrecoverable).
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import Session, col, select

from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.scripts.generate_trust_key import generate_trust_key


class TrustRegistrationError(Exception):
    """Base for trust registration failures."""


class EmptyTrustNameError(TrustRegistrationError):
    """Caller supplied a blank trust name."""


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


def register_trust(
    name: str,
    code: str | None,
    region: str | None,
    session: Session,
) -> RegisteredTrust:
    """Atomically register a trust: mint keys, claim an FL kit slot, insert the row.

    Args:
        name (str): Friendly display name (any non-empty string after strip).
        code (str | None): Optional short code (e.g. ``GSTT``).
        region (str | None): Optional NHS region.
        session (Session): SQLModel session; the function commits before returning.

    Returns:
        RegisteredTrust: The persisted trust, its assigned FL kit slot, and the
        plaintext api / internal-service keys (returned once — discard from memory
        immediately after handing them to the operator).

    Raises:
        EmptyTrustNameError: ``name.strip()`` is empty.
        DuplicateTrustError: A trust with this name already exists.
        NoFreeKitSlotError: The ``fl_kit_slot`` pool has no unassigned rows.
    """
    name = name.strip()
    if not name:
        raise EmptyTrustNameError("Trust name is required.")
    code = code.strip() if code else None
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
    slot = session.exec(
        select(FLKitSlot)
        .where(col(FLKitSlot.assigned_to_trust_id).is_(None))
        .order_by(col(FLKitSlot.slot_number).asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if slot is None:
        raise NoFreeKitSlotError(
            "No FL kit slots available. Pre-provision more slots in flip-fl-base "
            "and add them to FL_KIT_SLOT_NAMES."
        )

    session.add(trust)
    session.flush()  # populate trust.id before binding the slot
    slot.assigned_to_trust_id = trust.id
    slot.assigned_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(trust)
    session.refresh(slot)

    return RegisteredTrust(
        trust=trust,
        fl_kit_slot=slot,
        trust_api_key=api_key,
        trust_internal_service_key=internal_key,
    )
