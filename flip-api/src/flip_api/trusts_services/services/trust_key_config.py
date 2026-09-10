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

"""Startup audit of the hub's per-trust payload-encryption key configuration.

Per-trust keys are provisioned in two halves that nothing joins up: the trust gets
``TRUST_AES_KID`` / ``TRUST_AES_KEY_BASE64`` in its kit file, and the hub gets the
same key in its ``AES_TRUST_KEYS`` map. Miss the hub half and the two directions
disagree in a way that is easy to misread:

* **hub → trust** degrades **silently**. ``kid_for_trust()`` looks the trust's kid up
  in the keyring, does not find it, and falls back to the shared key. The trust
  decrypts it fine, because its keyring also holds the shared key. Everything works —
  with none of the isolation the configuration was supposed to buy, and no signal.
* **trust → hub** fails **loudly**, with ``KeyError: No key registered for kid
  'trust-<uuid>'`` at decrypt time.

The quiet direction is the dangerous one, so this reports at boot what the hub can
actually see: which trusts have a key, which are on the shared key, and — the usual
cause of a silent fallback — which configured kids match no trust at all, because a
typo'd or stale kid is indistinguishable at runtime from "this trust has no key yet".

What the hub cannot know is whether a trust has ``TRUST_AES_KID`` set in its kit;
that lives on the trust host. The orphan-kid check is the closest hub-side proxy.
"""

from dataclasses import dataclass, field
from uuid import UUID

from sqlmodel import Session, select

from flip_api.db.models.main_models import Trust
from flip_api.utils.encryption import SHARED_KID, registered_kids
from flip_api.utils.logger import logger

#: Prefix every per-trust kid carries — see ``kid_for_trust``.
_KID_PREFIX = "trust-"

#: Key lengths AES accepts, in bytes (AES-128 / AES-192 / AES-256).
_VALID_KEY_LENGTHS = (16, 24, 32)


@dataclass
class TrustKeyReport:
    """What the hub's keyring says about per-trust key coverage."""

    #: Names of trusts with a per-trust key loaded.
    covered: list[str] = field(default_factory=list)
    #: Names of trusts falling back to the shared key.
    on_shared_key: list[str] = field(default_factory=list)
    #: Configured kids matching no trust — a typo, or a deleted trust.
    orphan_kids: list[str] = field(default_factory=list)
    #: ``kid`` values whose key is not a valid AES length.
    bad_length_kids: list[str] = field(default_factory=list)

    @property
    def total_trusts(self) -> int:
        return len(self.covered) + len(self.on_shared_key)


def audit_trust_key_config(session: Session) -> TrustKeyReport:
    """Compare the hub's keyring against the registered trusts.

    Args:
        session (Session): Database session used to read the trust roster.

    Returns:
        TrustKeyReport: Coverage plus any misconfiguration found.

    Raises:
        json.JSONDecodeError: If ``AES_TRUST_KEYS`` is not valid JSON.
        binascii.Error: If a configured key is not valid base64.
    """
    # Also forces the lazy keyring parse, so a malformed AES_TRUST_KEYS raises here
    # (at boot) instead of on the first request that needs to encrypt.
    kids = registered_kids()

    trusts = session.exec(select(Trust)).all()
    report = TrustKeyReport()

    matched_kids: set[str] = {SHARED_KID}
    for trust in trusts:
        # Mirrors kid_for_trust's lookup order: id first, then code.
        candidates = [f"{_KID_PREFIX}{trust.id}"]
        if trust.code:
            candidates.append(f"{_KID_PREFIX}{trust.code}")
        hit = next((c for c in candidates if c in kids), None)
        if hit:
            matched_kids.add(hit)
            report.covered.append(trust.name)
        else:
            report.on_shared_key.append(trust.name)

    for kid, length in kids.items():
        if kid not in matched_kids:
            report.orphan_kids.append(kid)
        if length not in _VALID_KEY_LENGTHS:
            report.bad_length_kids.append(kid)

    return report


def log_trust_key_config(session: Session) -> TrustKeyReport:
    """Run the audit and log it. Never raises on a misconfiguration — it reports.

    A missing per-trust key is the normal state during a staged rollout, so coverage
    is informational. An orphan kid or a bad key length is an operator error that
    would otherwise only surface as a silent shared-key fallback, so both are logged
    at error level.

    Args:
        session (Session): Database session used to read the trust roster.

    Returns:
        TrustKeyReport: The report that was logged, for callers that want to assert on it.
    """
    report = audit_trust_key_config(session)

    logger.info(
        "Per-trust payload keys: %d/%d trusts have their own key; %d using the shared key.",
        len(report.covered),
        report.total_trusts,
        len(report.on_shared_key),
    )

    if report.orphan_kids:
        logger.error(
            "AES_TRUST_KEYS contains %d kid(s) matching no registered trust: %s. "
            "Those keys are never selected, so the trusts they were meant for keep using "
            "the shared key with no other symptom. Check for a typo or a deleted trust.",
            len(report.orphan_kids),
            ", ".join(sorted(report.orphan_kids)),
        )

    if report.bad_length_kids:
        logger.error(
            "AES_TRUST_KEYS contains %d key(s) that are not a valid AES length (16/24/32 bytes): %s. "
            "Encrypting to these trusts will fail at request time.",
            len(report.bad_length_kids),
            ", ".join(sorted(report.bad_length_kids)),
        )

    return report


def kid_for_trust_id(trust_id: UUID) -> str:
    """Return the kid a trust's key would be registered under.

    Args:
        trust_id (UUID): The trust's id.

    Returns:
        str: The ``trust-<uuid>`` kid, matching what ``register_trust`` mints.
    """
    return f"{_KID_PREFIX}{trust_id}"
