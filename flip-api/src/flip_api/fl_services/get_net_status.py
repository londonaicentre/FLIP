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

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.fl import IClientStatus, INetStatus
from flip_api.fl_services.get_status import fetch_client_status
from flip_api.fl_services.services.fl_scheduler_service import get_net_by_name
from flip_api.trusts_services.services.trust import get_trusts
from flip_api.utils.logger import logger

router = APIRouter(prefix="/fl", tags=["fl_services"])


# [#114] ✅
@router.get("/{net_name}/status", response_model=INetStatus)
def get_net_status(
    net_name: str,
    request: Request,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
):
    """
    Get the status of a net and its clients. A net consists of a central controller with a worker at each of the Trusts.

    Args:
        net_name: The name of the network to get status for
        request: FastAPI request object
        db: Database session

    Returns:
        INetStatus: Object containing the network name and status of connected clients
    """
    request_id = str(request.scope.get("request_id", "req-id"))

    try:
        # Get network information by name
        net_info = get_net_by_name(net_name, db)

        if not net_info:
            raise HTTPException(status_code=404, detail=f"No net could be found for {net_name}")

        logger.info(f"Retrieving status for net: {net_name} with info {net_info}")

        # Get NVFlare client status
        clients = fetch_client_status(request_id, net_info.endpoint)

        if not clients:
            error_message = f"No response from FL API for net {net_name}"
            logger.error(error_message)
            raise HTTPException(status_code=502, detail=error_message)

        # For each net, we would like to know which Trusts are connected and their statuses.
        trusts = get_trusts(db)
        trust_client_statuses: List[IClientStatus] = []
        for trust in trusts:
            connected_client_info = None

            for client in clients:
                # TODO Trust name and FL client name should match ??
                if client.name == trust.name:
                    connected_client_info = client
                    break
            else:
                logger.warning(f"Trust {trust.name} not found in client statuses")
                trust_client_statuses.append(
                    IClientStatus(name=trust.name, online=False, status="Client not connected")
                )
                continue

            # Log the trust and connected client information
            logger.debug(f"Trust {trust} name: {trust.name}, connected client info: {connected_client_info}")
            trust_client_statuses.append(connected_client_info)

        # Create net status response
        net_status = INetStatus(
            name=net_name,
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
