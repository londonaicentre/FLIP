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

"""Automatic-upgrade routes: the portal's (Trust Admin) and the host agent's (trust API key)."""

from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, status
from sqlmodel import Session

from flip_api.auth.access_manager import authenticate_trust
from flip_api.auth.auth_utils import has_permissions, has_trust_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.schemas.trusts import (
    AutoUpgradeSettings,
    AutoUpgradeView,
    UpgradeLease,
    UpgradePolicy,
    UpgradeStatusInput,
)
from flip_api.trusts_services.services import auto_upgrade
from flip_api.utils.logger import logger
from flip_api.utils.rate_limiter import limiter

router = APIRouter(tags=["trusts_services"])


def _own_trust(trust_id: UUID, user_id: UUID, db: Session) -> Trust:
    """The trust, if the caller is its Trust Admin, or a hub admin who manages deployments (support).

    Unlike approving a project, an upgrade setting grants no access to the trust's data, so the hub-wide grant is
    enough here and ``has_trust_permissions``' exclusion of global roles does not apply to the second branch.
    """
    trust = db.get(Trust, trust_id)
    if trust is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Trust {trust_id} not found")
    if not (
        has_trust_permissions(user_id, [PermissionRef.CAN_MANAGE_TRUST_UPGRADES], trust_id, db)
        or has_permissions(user_id, [PermissionRef.CAN_MANAGE_DEPLOYMENTS], db)
    ):
        logger.error(f"User {user_id} may not manage automatic upgrades of trust {trust_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User with ID: {user_id} may not manage this trust's upgrades",
        )
    return trust


@router.get(
    "/trust/{trust_id}/auto-upgrade",
    summary="A trust's automatic-upgrade settings and status (for its Trust Admin).",
    response_model=AutoUpgradeView,
    response_model_by_alias=True,
)
def get_auto_upgrade(
    trust_id: UUID = Path(..., description="The trust."),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> AutoUpgradeView:
    """The settings and the status the My Trust card shows.

    Raises:
        HTTPException: 404 if the trust does not exist; 403 unless the caller may manage this trust's upgrades.
    """
    return auto_upgrade.view_for_admin(_own_trust(trust_id, user_id, db), db)


@router.put(
    "/trust/{trust_id}/auto-upgrade",
    summary="Switch a trust's automatic upgrades on or off and set its maintenance window.",
    response_model=AutoUpgradeView,
    response_model_by_alias=True,
)
def put_auto_upgrade(
    trust_id: UUID = Path(..., description="The trust."),
    settings: AutoUpgradeSettings = Body(...),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> AutoUpgradeView:
    """Store the switch and the window. An upgrade already running finishes; this applies from the next release.

    Raises:
        HTTPException: 404 if the trust does not exist; 403 unless the caller may manage this trust's upgrades;
            422 for an incomplete window or an unknown time zone.
    """
    trust = _own_trust(trust_id, user_id, db)
    auto_upgrade.apply_settings(trust, settings, db)
    logger.info(f"User {user_id} set automatic upgrades of trust {trust.name}: enabled={settings.enabled}")
    return auto_upgrade.view_for_admin(trust, db)


@router.post(
    "/trust/{trust_id}/auto-upgrade/clear-failure",
    summary="Allow a release that failed to be tried again, once an operator has checked the trust.",
    response_model=AutoUpgradeView,
    response_model_by_alias=True,
)
def clear_auto_upgrade_failure(
    trust_id: UUID = Path(..., description="The trust."),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> AutoUpgradeView:
    """Clear a FAILED attempt so the agent may retry that release.

    Raises:
        HTTPException: 404/403 as for the other routes; 409 if no failed upgrade is recorded.
    """
    trust = _own_trust(trust_id, user_id, db)
    auto_upgrade.clear_failure(trust, db)
    return auto_upgrade.view_for_admin(trust, db)


# --- The host agent: authenticated with the trust's own API key, like the task poll. ---


@router.get(
    "/trust/upgrade-policy",
    summary="The authenticated trust's upgrade policy, for its host agent.",
    response_model=UpgradePolicy,
)
@limiter.limit("30/minute")
def get_upgrade_policy(
    request: Request,
    db: Session = Depends(get_session),
    authenticated_trust: Trust = Depends(authenticate_trust),
) -> UpgradePolicy:
    """Policy for one agent tick. Also records that the agent is alive and since when the trust has been behind."""
    auto_upgrade.note_agent_tick(authenticated_trust, db)
    return auto_upgrade.policy_for_agent(authenticated_trust, db)


@router.post(
    "/trust/upgrade-status",
    summary="The host agent claims an upgrade (STARTING) or reports how it ended.",
    response_model=UpgradeLease | None,
)
@limiter.limit("30/minute")
def post_upgrade_status(
    request: Request,
    report: UpgradeStatusInput = Body(...),
    db: Session = Depends(get_session),
    authenticated_trust: Trust = Depends(authenticate_trust),
) -> UpgradeLease | None:
    """STARTING claims the lease (409 if refused); SUCCEEDED and FAILED release it and record the attempt."""
    return auto_upgrade.record_status(authenticated_trust.id, report, db)
