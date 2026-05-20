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

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.domain.interfaces.fl import IClientStatus, INetStatus
from flip_api.domain.schemas.status import ClientStatus
from flip_api.fl_services.get_status import fetch_client_status
from flip_api.fl_services.services.fl_scheduler_service import get_net_by_name, get_slot_names_by_trust_ids
from flip_api.trusts_services.services.trust import get_trusts
from flip_api.utils.logger import logger

router = APIRouter(prefix="/fl", tags=["fl_services"])


# [#114] ✅
@router.get("/{net_name}/status", response_model=INetStatus)
def get_net_status(
    net_name: str,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
):
    """
    Get the status of a net and its clients. A net consists of a central controller with a worker at each of the Trusts.

    Args:
        net_name (str): The name of the network to get status for.
        db (Session): Database session.
        user_id (UUID): ID of the authenticated user.

    Returns:
        INetStatus: Object containing the network name and status of connected clients.

    Raises:
        HTTPException: If the net is not found, if there is no response from the FL API, or if there is an error
        retrieving the net status.
    """

    try:
        # Get network information by name
        net_info = get_net_by_name(net_name, db)

        if not net_info:
            raise HTTPException(status_code=404, detail=f"No net could be found for {net_name}")

        logger.info(f"Retrieving status for net: {net_name} with info {net_info}")

        # Get FL client status
        clients = fetch_client_status(net_info.endpoint)

        if not clients:
            error_message = f"No response from FL API for net {net_name}"
            logger.error(error_message)
            raise HTTPException(status_code=502, detail=error_message)

        # Match by FL kit slot name — the FL net never sees Trust.name. See the equivalent
        # comment in get_status.py for the rationale (TRUST_DISPLAY_NAMES can rename a trust
        # without changing its slot identity).
        trusts = get_trusts(db)
        slot_names_by_trust_id = get_slot_names_by_trust_ids([t.id for t in trusts], db)
        trust_client_statuses: list[IClientStatus] = []
        for trust in trusts:
            slot_name = slot_names_by_trust_id.get(trust.id)
            matched = next((c for c in clients if slot_name and c.name == slot_name), None)
            if matched is None:
                logger.warning(f"Trust {trust.name} (slot={slot_name}) not found in client statuses")
                trust_client_statuses.append(IClientStatus(name=trust.name, status=ClientStatus.NO_REPLY.value))
                continue
            logger.debug(f"Trust {trust.name} matched slot {slot_name} → status {matched.status}")
            trust_client_statuses.append(IClientStatus(name=trust.name, status=matched.status))

        # Create net status response
        net_status = INetStatus(
            name=net_name,
            fl_backend=get_settings().FL_BACKEND,
            online=True,  # Assuming the net is online if we reach this point
            registered_clients=len(trust_client_statuses),
            clients=trust_client_statuses,
        )

        logger.info(f"{net_name} status result: {net_status}")

        return net_status

    except HTTPException:
        raise
    except Exception as error:
        logger.error(f"Error retrieving net status: {str(error)}")
        raise HTTPException(status_code=500, detail=str(error))
