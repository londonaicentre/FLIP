# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Automatic upgrades of a trust: policy, lease, and the status the portal shows.

The hub owns the switch, the target release and the idle decision; the agent on the trust host owns execution
(see ``docs/source/sys-admin/admin-trust-auto-upgrade.rst``). Everything here is plain functions over a ``Session``
so the routers stay thin and the rules are testable without HTTP.
"""

import re
from datetime import datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from flip_api.db.models.main_models import FLJob, FLJobTrust, Trust
from flip_api.domain.schemas.status import JobStatus
from flip_api.domain.schemas.trusts import (
    AutoUpgradeLastAttempt,
    AutoUpgradeSettings,
    AutoUpgradeView,
    UpgradeLease,
    UpgradePolicy,
    UpgradeStatusInput,
    UpgradeWindow,
)
from flip_api.utils.logger import logger
from flip_api.utils.version import build_identity

# A claim that is never finished (the host died) frees the trust after this long.
LEASE_TTL = timedelta(hours=2)
# The agent ticks hourly; two missed ticks and a bit of slack means it is not running.
AGENT_STALE_AFTER = timedelta(hours=3)

# Blocker codes shown in the portal (mirrored by flip-ui's AutoUpgradeBlocker).
JOBS_RUNNING = "JOBS_RUNNING"
OUTSIDE_WINDOW = "OUTSIDE_WINDOW"
LOCAL_MODIFICATIONS = "LOCAL_MODIFICATIONS"
AGENT_OFFLINE = "AGENT_OFFLINE"
# What a host agent may report under BLOCKED.
REPORTABLE_BLOCKERS = frozenset({LOCAL_MODIFICATIONS, "UNSUPPORTED_MODE", "UNKNOWN_CHECKOUT"})

# Only an immutable CI tag is comparable between the hub and a trust (mirrors flip-ui's isImageTag).
_IMAGE_TAG = re.compile(r"^v\d+\.\d+\.\d+([-.][0-9A-Za-z.-]+)?$")


def _now() -> datetime:
    """Naive UTC, the convention of the ``trust`` table's timestamp columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="milliseconds") + "Z" if value else None


def target_release() -> str | None:
    """The release a trust upgrades to: the one this hub runs, and only if it is a release tag.

    A ``sha-`` build or a local build names no release a site can check out, so it yields ``None`` and the agent
    does nothing until the hub is on a tag.
    """
    identity = build_identity()
    return identity if identity and _IMAGE_TAG.match(identity) else None


def current_release(trust: Trust) -> str | None:
    """The release the trust runs, from the trust-api version in its last health snapshot."""
    services = trust.services_health or {}
    entry = services.get("trust-api") if isinstance(services, dict) else None
    version = entry.get("version") if isinstance(entry, dict) else None
    return version if isinstance(version, str) and _IMAGE_TAG.match(version) else None


def is_busy(trust_id: UUID, db: Session) -> bool:
    """True while a training job that involves this trust is running (queued jobs, imaging and cohorts do not count)."""
    running = db.exec(
        select(FLJob.id)
        .join(FLJobTrust, col(FLJobTrust.fl_job_id) == col(FLJob.id))
        .where(FLJobTrust.trust_id == trust_id)
        .where(FLJob.status == JobStatus.IN_PROGRESS)
        .limit(1)
    ).first()
    return running is not None


def lease_active(trust: Trust, now: datetime | None = None) -> bool:
    """True while the trust's upgrade lease is held and has not expired."""
    return trust.upgrade_lease_expires_at is not None and trust.upgrade_lease_expires_at > (now or _now())


def in_window(now: datetime, start: str | None, end: str | None, tz_name: str | None) -> bool:
    """Whether ``now`` (naive UTC) falls inside the trust-local window; no window means always.

    ``start > end`` is an overnight window (22:00-04:00). Evaluated in the trust's own zone so it follows
    daylight saving time.
    """
    if not (start and end and tz_name):
        return True
    local = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name)).time().replace(second=0, microsecond=0)
    s, e = time.fromisoformat(start), time.fromisoformat(end)
    return s <= local < e if s < e else (local >= s or local < e)


def _is_stale(moment: datetime | None, now: datetime) -> bool:
    return moment is None or now - moment > AGENT_STALE_AFTER


def failed_release(trust: Trust) -> str | None:
    """The release whose last attempt FAILED and has not been cleared by an operator."""
    return trust.last_upgrade_release if trust.last_upgrade_state == "FAILED" else None


def note_agent_tick(trust: Trust, db: Session) -> None:
    """Record that the agent asked for its policy, and when the trust first fell behind the hub."""
    now = _now()
    trust.upgrade_agent_seen_at = now
    target, current = target_release(), current_release(trust)
    if target and current and current != target:
        trust.upgrade_pending_since = trust.upgrade_pending_since or now
    else:
        trust.upgrade_pending_since = None
    db.add(trust)
    db.commit()


def policy_for_agent(trust: Trust, db: Session) -> UpgradePolicy:
    """What the host agent is told on each tick."""
    window = (
        UpgradeWindow(start=trust.auto_upgrade_window_start, end=trust.auto_upgrade_window_end)
        if trust.auto_upgrade_window_start and trust.auto_upgrade_window_end
        else None
    )
    now = _now()
    lease = (
        UpgradeLease(release=trust.upgrade_lease_release or "", expires_at=_iso(trust.upgrade_lease_expires_at) or "")
        if lease_active(trust, now)
        else None
    )
    return UpgradePolicy(
        enabled=trust.auto_upgrade_enabled,
        target_release=target_release(),
        maintenance_window=window,
        timezone=trust.auto_upgrade_timezone,
        is_busy=is_busy(trust.id, db),
        lease=lease,
        failed_release=failed_release(trust),
    )


def view_for_admin(trust: Trust, db: Session) -> AutoUpgradeView:
    """The settings and status the My Trust card shows."""
    now = _now()
    target, current = target_release(), current_release(trust)
    behind = bool(target and current and current != target)
    failed = failed_release(trust)

    blockers: list[str] = []
    if lease_active(trust, now):
        state = "IN_PROGRESS"
    elif failed is not None:
        state = "FAILED"
    elif behind:
        state = "PENDING"
        if trust.auto_upgrade_enabled:
            if is_busy(trust.id, db):
                blockers.append(JOBS_RUNNING)
            if not in_window(
                now, trust.auto_upgrade_window_start, trust.auto_upgrade_window_end, trust.auto_upgrade_timezone
            ):
                blockers.append(OUTSIDE_WINDOW)
            if trust.upgrade_blocker == LOCAL_MODIFICATIONS and not _is_stale(trust.upgrade_blocker_at, now):
                blockers.append(LOCAL_MODIFICATIONS)
            if _is_stale(trust.upgrade_agent_seen_at, now):
                blockers.append(AGENT_OFFLINE)
    elif trust.last_upgrade_state == "SUCCEEDED" and trust.last_upgrade_release == current:
        state = "SUCCEEDED"
    else:
        state = "UP_TO_DATE"

    last = (
        AutoUpgradeLastAttempt(
            state=trust.last_upgrade_state,
            release=trust.last_upgrade_release or "",
            at=_iso(trust.last_upgrade_at) or "",
            detail=trust.last_upgrade_detail if trust.last_upgrade_state == "FAILED" else None,
        )
        if trust.last_upgrade_state in ("SUCCEEDED", "FAILED") and trust.last_upgrade_at
        else None
    )
    return AutoUpgradeView(
        enabled=trust.auto_upgrade_enabled,
        target_release=target,
        current_release=current,
        window_start=trust.auto_upgrade_window_start,
        window_end=trust.auto_upgrade_window_end,
        timezone=trust.auto_upgrade_timezone,
        state=state,
        blockers=blockers,
        pending_since=_iso(trust.upgrade_pending_since) if behind else None,
        last_attempt=last,
    )  # type: ignore[call-arg]


def apply_settings(trust: Trust, settings: AutoUpgradeSettings, db: Session) -> None:
    """Store the Trust Admin's switch and window. An upgrade already running is untouched: it finishes."""
    trust.auto_upgrade_enabled = settings.enabled
    trust.auto_upgrade_window_start = settings.window_start
    trust.auto_upgrade_window_end = settings.window_end
    trust.auto_upgrade_timezone = settings.timezone
    db.add(trust)
    db.commit()


def clear_failure(trust: Trust, db: Session) -> None:
    """An operator has looked at a failed upgrade: allow the release to be tried again."""
    if trust.last_upgrade_state != "FAILED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="There is no failed upgrade to clear.")
    trust.last_upgrade_state = None
    db.add(trust)
    db.commit()


def record_status(trust_id: UUID, report: UpgradeStatusInput, db: Session) -> UpgradeLease | None:
    """Apply the agent's report. STARTING claims the lease; the other states release it.

    The claim takes a row lock on the trust, and the scheduler takes the same lock before it assigns a job, so
    "idle now" and "a job is assigned a second later" cannot both be true.

    Args:
        trust_id (UUID): The authenticated trust.
        report (UpgradeStatusInput): The agent's report.
        db (Session): Database session.

    Returns:
        UpgradeLease | None: The lease granted for STARTING, else ``None``.

    Raises:
        HTTPException: 409 if a claim is refused (switch off, a job running, the release failed before and was not
            cleared, or another claim is live).
    """
    trust = db.exec(select(Trust).where(Trust.id == trust_id).with_for_update()).one()
    now = _now()

    if report.state == "STARTING":
        if not trust.auto_upgrade_enabled:
            raise HTTPException(status.HTTP_409_CONFLICT, "Automatic upgrades are switched off for this trust.")
        if failed_release(trust) == report.release:
            raise HTTPException(status.HTTP_409_CONFLICT, "This release failed before and has not been cleared.")
        if lease_active(trust, now) and trust.upgrade_lease_release != report.release:
            raise HTTPException(status.HTTP_409_CONFLICT, "Another upgrade holds the lease.")
        if is_busy(trust.id, db):
            raise HTTPException(status.HTTP_409_CONFLICT, "A training job that uses this trust is running.")
        trust.upgrade_lease_release = report.release
        trust.upgrade_lease_expires_at = now + LEASE_TTL
        trust.upgrade_blocker = None
        db.add(trust)
        db.commit()
        logger.info(f"Trust {trust.name}: upgrade to {report.release} claimed")
        return UpgradeLease(release=report.release, expires_at=_iso(trust.upgrade_lease_expires_at) or "")

    if report.state == "BLOCKED":
        trust.upgrade_blocker = (report.detail or "UNKNOWN")[:64] if (report.detail in REPORTABLE_BLOCKERS) else None
        trust.upgrade_blocker_at = now
        db.add(trust)
        db.commit()
        return None

    # SUCCEEDED / FAILED: release the lease and record the attempt.
    trust.upgrade_lease_expires_at = None
    trust.upgrade_lease_release = None
    trust.last_upgrade_state = report.state
    trust.last_upgrade_release = report.release
    trust.last_upgrade_at = now
    trust.last_upgrade_detail = report.detail if report.state == "FAILED" else None
    trust.upgrade_blocker = None
    db.add(trust)
    db.commit()
    logger.info(f"Trust {trust.name}: upgrade to {report.release} {report.state}")
    return None
