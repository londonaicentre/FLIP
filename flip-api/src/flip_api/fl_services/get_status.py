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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.fl import (
    IClientStatus,
    INetDetails,
    INetStatus,
)
from flip_api.domain.interfaces.trust import ITrust
from flip_api.domain.schemas.status import ClientStatus, ServerEngineStatus
from flip_api.fl_services.services.fl_scheduler_service import get_nets, get_slot_names_by_trust_ids
from flip_api.fl_services.services.fl_service import fetch_client_status, fetch_server_status
from flip_api.trusts_services.services.trust import get_trusts
from flip_api.utils.logger import logger

router = APIRouter(prefix="/fl", tags=["fl_services"])


def _offline_net_status(net: INetDetails) -> INetStatus:
    return INetStatus(
        name=net.name,
        fl_backend=net.fl_backend,
        online=False,
        registered_clients=0,
        clients=[],
        net_in_use=False,
    )


def match_trust_clients(
    trusts: list[ITrust], slot_names_by_trust_id: dict[UUID, str], clients: list[IClientStatus]
) -> list[IClientStatus]:
    """Report each trust with the status of the FL client on its kit slot, or NO_REPLY.

    Clients are matched on the FL kit slot name (not Trust.name): the FL net only ever sees the slot's
    CN, which is independent of the trust's hub-side display name. The result still carries trust.name
    so the UI shows the friendly name.
    """
    trust_client_statuses: list[IClientStatus] = []
    for trust in trusts:
        slot_name = slot_names_by_trust_id.get(trust.id)
        matched = next((c for c in clients if slot_name and c.name == slot_name), None)
        if matched is None:
            logger.warning(f"Trust {trust.name} (slot={slot_name}) not found in client statuses")
        else:
            logger.debug(f"Trust {trust.name} matched slot {slot_name} → status {matched.status}")
        trust_client_statuses.append(
            IClientStatus(
                name=trust.name,
                code=trust.code,
                status=matched.status if matched else ClientStatus.NO_REPLY,
                fl_kit_slot=slot_name,
            )
        )
    return trust_client_statuses


# [#114] ✅
@router.get("/status", response_model=list[INetStatus])
def get_status_endpoint(
    request: Request,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> list[INetStatus]:
    """
    Retrieve the status of all federated learning networks.

    This endpoint fetches the status of all networks, including server and client statuses, and returns a list of
    INetStatus objects representing each network's status.

    Args:
        request (Request): FastAPI request object.
        db (Session): Database session.
        user_id (UUID): ID of the authenticated user.

    Returns:
        list[INetStatus]: A list of INetStatus objects containing the status of each network.

    Raises:
        HTTPException: If there is an error while retrieving the net statuses.
    """

    try:
        nets = get_nets(db)

        net_statuses: list[INetStatus] = []

        for net in nets:
            server_status = fetch_server_status(net.endpoint)
            logger.info({"server status response": server_status})

            if not server_status:
                logger.error(f"{net.name}: No response from FL API")
                net_statuses.append(_offline_net_status(net))
                continue

            # Fetch client statuses
            clients = fetch_client_status(net.endpoint)

            if not clients:
                logger.error(f"{net.name}: No clients connected")
                net_statuses.append(_offline_net_status(net))
                continue

            # For each net, we would like to know which Trusts are connected and their statuses.
            trusts = get_trusts(db)
            slot_names_by_trust_id = get_slot_names_by_trust_ids([t.id for t in trusts], db)
            trust_client_statuses = match_trust_clients(trusts, slot_names_by_trust_id, clients)

            # Create net status response
            net_statuses.append(
                INetStatus(
                    name=net.name,
                    # Report the canonical seeded backend (set from FL_BACKEND, never reconciled at runtime).
                    fl_backend=net.fl_backend,
                    online=True,  # we got a response, so the server is online
                    registered_clients=len(trust_client_statuses),
                    net_in_use=server_status.status in [ServerEngineStatus.STARTING, ServerEngineStatus.STARTED],
                    clients=trust_client_statuses,
                )
            )

        return net_statuses

    except Exception as e:
        logger.exception("Error while retrieving net statuses")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e
