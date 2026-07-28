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

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import FLNets, FLScheduler
from flip_api.domain.interfaces.fl import IQuiesceStatus, ISchedulerStatus
from flip_api.domain.schemas.status import NetStatus
from flip_api.utils.site_manager import is_deployment_mode_enabled

router = APIRouter(prefix="/fl", tags=["fl_services"])


@router.get("/quiesce", response_model=IQuiesceStatus)
def get_quiesce_status(
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> IQuiesceStatus:
    """
    Report whether the platform is quiesced for a Central Hub redeploy.

    An ECS replacement kills an in-flight training run, so the safe-redeploy condition is
    deployment mode enabled AND no net's scheduler BUSY (FLIP#770). ``make deploy-centralhub``
    prints a reminder pointing operators at this endpoint to verify both before deploying.

    Args:
        db (Session): Database session.
        user_id (UUID): ID of the authenticated user.

    Returns:
        IQuiesceStatus: Deployment-mode flag, overall quiesce state, and per-net scheduler status.
    """
    rows = db.exec(select(FLScheduler, FLNets.name).join(FLNets, FLScheduler.net_id == FLNets.id)).all()  # type: ignore[arg-type]

    schedulers = [ISchedulerStatus(net=net_name, status=scheduler.status) for scheduler, net_name in rows]

    return IQuiesceStatus(
        deployment_mode=is_deployment_mode_enabled(db),
        fl_quiesced=all(s.status != NetStatus.BUSY for s in schedulers),
        schedulers=schedulers,
    )
