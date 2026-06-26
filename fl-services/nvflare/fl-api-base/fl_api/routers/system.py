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

# System and service status functions
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from nvflare.fuel.hci.client.fl_admin_api import TargetType

from fl_api.core.dependencies import get_session
from fl_api.utils.flip_session import FLIP_Session
from fl_api.utils.schemas import ClientInfoModel, ServerInfoModel, SystemInfoModel

router = APIRouter()


@router.get("/check_server_status", response_model=ServerInfoModel)
def check_server_status(session: FLIP_Session = Depends(get_session)) -> ServerInfoModel:
    """
    Checks the status of the server.

    Args:
        session (FLIP_Session): the FLIP session instance.

    Returns:
        ServerInfoModel: status information about the server.
    """
    return session.check_server_status()


@router.get("/check_client_status", response_model=List[ClientInfoModel])
def check_client_status(
    targets: Optional[List[str]] = Query(None),
    session: FLIP_Session = Depends(get_session),
) -> List[ClientInfoModel]:
    """
    Checks the status of specified clients or all clients if no specific targets are provided.

    Args:
        targets (Optional[List[str]]): list of client names to check status for. If not specified, the status of all
        clients will be checked.
        session (FLIP_Session): the FLIP session instance.

    Returns:
        List[ClientInfoModel]: a list of ClientInfoModel objects containing client status information.
    """
    return session.check_client_status(targets)


@router.get("/get_system_info", response_model=SystemInfoModel)
def get_system_info(session: FLIP_Session = Depends(get_session)) -> SystemInfoModel:
    """
    Get system info of the FL system.

    Args:
        session (FLIP_Session): the FLIP session instance.

    Returns:
        SystemInfoModel: system info of the FL system.
    """
    return session.get_system_info()


@router.get("/get_connected_client_list", response_model=List[ClientInfoModel])
def get_connected_client_list(session: FLIP_Session = Depends(get_session)) -> List[ClientInfoModel]:
    """
    List of the connected clients.

    Returns:
        List[ClientInfoModel]: a list of ClientInfoModel objects.
    """
    return session.get_connected_client_list()


@router.get("/get_working_directory/{target}", response_model=str)
def get_working_directory(target: str, session: FLIP_Session = Depends(get_session)) -> str:
    """
    Returns the working directory of the specified target.

    Args:
        target (str): target (e.g. site-1).

    Returns:
        str: current working directory of the specified target.
    """
    return session.get_working_directory(target)


@router.post("/restart/{target_type}")
def restart(
    target_type: TargetType,
    client_names: Optional[List[str]] = Query(None),
    session: FLIP_Session = Depends(get_session),
):
    """
    Restart specified system target(s).

    [WARNING]: restarting the server might cause the session to drop. You'll need to re-start the API.

    Args:
        target_type (TargetType): type of target to restart. Can be server, client or all.
        client_names (Optional[List[str]]): if target_type is client, this is a list of client names. If a target
        is not in the actual list of clients, it will be ignored.
        session (FLIP_Session): the FLIP session instance.

    Returns:
        dict: contains detailed info about the restart request:
            status - the overall status of the result.
            server_status - whether the server is restarted successfully - only if target_type is "all" or "server".
            client_status - a dict (keyed on client name) that specifies status of each client - only if target_type
                is "all" or "client".
    """
    return session.restart(
        target_type=target_type,
        client_names=client_names,
    )


@router.post("/shutdown/{target_type}", status_code=status.HTTP_200_OK)
def shutdown(
    target_type: TargetType,
    client_names: Optional[List[str]] = Query(None),
    session: FLIP_Session = Depends(get_session),
) -> None:
    """
    Shut down specific services that are part of the FL system.

    Args:
        target_type (TargetType): type of target. Either server, client or all.
        client_names (Optional[List[str]], optional): for target_type client, this is a list of specific clients you
        want to shut down. If not specified, all clients will be shut down.
        session (FLIP_Session): the FLIP session instance.

    Returns:
        None
    """
    return session.shutdown(
        target_type=target_type,
        client_names=client_names,
    )


@router.post("/shutdown_system", status_code=status.HTTP_200_OK)
def shutdown_system(session: FLIP_Session = Depends(get_session)) -> None:
    """
    Shuts down the whole FL system.

    Args:
        session (FLIP_Session): the FLIP session instance.

    Returns:
        None
    """
    return session.shutdown_system()
