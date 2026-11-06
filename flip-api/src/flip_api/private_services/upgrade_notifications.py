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

"""Email notifications for automatic trust upgrades (FLIP#1640).

An unattended upgrade is the one case where the people accountable for a trust
are, by design, not watching: it runs in a maintenance window chosen because
nobody is working. These emails are the out-of-band trace of that — one when an
upgrade claims the lease, one when it resolves.

Delivery is **best-effort and never load-bearing**. Every failure is logged and
swallowed, so no email fault can abort or delay the upgrade record the agent is
reporting. That is a deliberate departure from
``private_services/imaging_notifications``, which raises on a systemic failure
so its TrustTask stays queued: there, the email *is* the delivery mechanism for
a one-time credential and losing it loses the only copy; here the Trust panel
holds the same facts permanently and the email is a convenience. The
consequence is accepted and worth stating plainly: a lost FAILED email means
nobody is paged, and the operator learns from the panel instead.
"""

from uuid import UUID

from sqlmodel import Session, col, select

from flip_api.auth.identity.factory import build_identity_provider
from flip_api.config import get_settings
from flip_api.db.models.main_models import Trust
from flip_api.db.models.user_models import RoleRef, UserRole
from flip_api.utils.constants import UPGRADE_FINISHED_TEMPLATE_NAME, UPGRADE_STARTED_TEMPLATE_NAME
from flip_api.utils.email_sender import send_templated_email
from flip_api.utils.logger import logger

# A FAILED report carries a captured log tail (up to 8000 chars). Emails quote
# only the end of it: the tail is where the error is, and a multi-page body in
# an SES template helps nobody.
_MAX_DETAIL_CHARS = 1200


def notify_upgrade_started(trust: Trust, release: str, db: Session) -> None:
    """Tell this trust's admins (and the platform admin) that an upgrade has begun."""
    _dispatch(
        trust,
        UPGRADE_STARTED_TEMPLATE_NAME,
        {"trust_name": trust.name, "trust_code": trust.code, "release": release},
        db,
    )


def notify_upgrade_finished(trust: Trust, release: str, state: str, detail: str | None, db: Session) -> None:
    """Tell them how it ended.

    Args:
        trust (Trust): The trust that was upgraded.
        release (str): The release that was attempted.
        state (str): ``"SUCCEEDED"`` or ``"FAILED"``.
        detail (str | None): The agent's log tail on failure, trimmed for the email.
        db (Session): Database session.

    Returns:
        None
    """
    _dispatch(
        trust,
        UPGRADE_FINISHED_TEMPLATE_NAME,
        {
            "trust_name": trust.name,
            "trust_code": trust.code,
            "release": release,
            "state": state,
            "succeeded": state == "SUCCEEDED",
            "detail": _trim(detail),
        },
        db,
    )


def _trim(detail: str | None) -> str:
    """The tail end of a log capture, which is where the failure is."""
    if not detail:
        return ""
    collapsed = detail.strip()
    if len(collapsed) <= _MAX_DETAIL_CHARS:
        return collapsed
    return f"…{collapsed[-_MAX_DETAIL_CHARS:]}"


def _dispatch(trust: Trust, template_name: str, template_data: dict, db: Session) -> None:
    """Send one template to every recipient, swallowing all failures.

    Each recipient is sent independently so one rejected address cannot
    suppress the others.
    """
    try:
        recipients = _recipients(trust.id, db)
    except Exception as e:
        # Resolving recipients reaches the identity provider, which is a
        # network call that can fail on its own. Nothing about that should
        # surface to the agent reporting an upgrade.
        logger.exception(f"Trust {trust.name}: could not resolve upgrade-email recipients: {e}")
        return

    for recipient in recipients:
        try:
            send_templated_email(recipient=recipient, template_name=template_name, template_data=template_data)
            logger.info(f"Trust {trust.name}: upgrade email '{template_name}' dispatched to {recipient}")
        except Exception as e:
            # exception(), not error(): the traceback separates a rejected
            # address from a missing template or absent SES credentials.
            logger.exception(f"Trust {trust.name}: upgrade email to {recipient} failed: {e}")


def _recipients(trust_id: UUID, db: Session) -> list[str]:
    """This trust's enabled Trust Admins, plus the platform admin address.

    Trust Admin grants are trust-scoped ``UserRole`` rows (``trust_id`` set,
    FLIP#1260); the addresses themselves live in the identity provider, not in
    a local users table, so they are read through it rather than joined to.
    Disabled accounts are filtered out — a revoked admin should not keep
    receiving a trust's operational mail.

    The platform admin address is always included and is deduplicated against
    the admins, so a small deployment where the two coincide gets one email.
    """
    admin_ids = list(
        db.exec(
            select(col(UserRole.user_id))
            .where(col(UserRole.trust_id) == trust_id)
            .where(col(UserRole.role_id) == RoleRef.TRUST_ADMIN.value)
        ).all()
    )

    addresses: list[str] = []
    if admin_ids:
        provider = build_identity_provider()
        by_id = {user.id: user for user in provider.list_users()}
        for user_id in admin_ids:
            user = by_id.get(user_id)
            if user is None or user.is_disabled:
                logger.warning(f"Trust admin {user_id} is disabled or unknown — not sending the upgrade email")
                continue
            addresses.append(str(user.email))

    addresses.append(str(get_settings().AWS_SES_ADMIN_EMAIL_ADDRESS))

    # dict.fromkeys: dedupe, case-insensitively, without losing the order
    # (the trust's own admins read before the central mailbox).
    seen: dict[str, str] = {}
    for address in addresses:
        seen.setdefault(address.lower(), address)
    return list(seen.values())
