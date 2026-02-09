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

# System and service status functions
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from nvflare.fuel.hci.client.fl_admin_api import (
    TargetType,
)

from fl_api.core.dependencies import get_session
from fl_api.utils.flip_session import FLIP_Session
from fl_api.utils.logger import logger

router = APIRouter()


@router.get("/check_status/{target_type}")
def check_status(
    target_type: TargetType,
    targets: Optional[List[str]] = Query(None),
    session: FLIP_Session = Depends(get_session),
):
    """
    Checks the status of the server, clients or full FL system.

    Args:
        target_type (TargetType): type of target (can be server, client or all)
        targets (Optional[List[str]]): if target_type is client, this is a list of specific clients you want to check
        the status of. If not specified, the status of all clients will be checked.
        session (FLIP_Session): the FLIP session instance.

    Returns:
        List[ClientInfoModel] | ServerInfo | SystemInfo: status information about the specified target(s).
    """
    logger.info(f"Checking status of {target_type} with targets: {targets}")

    if target_type == TargetType.CLIENT:
        if targets:
            return session.check_client_status(targets)
        return session.check_client_status()

    elif target_type == TargetType.SERVER:
        return session.check_server_status()

    elif target_type == TargetType.ALL:
        return session.get_system_info()

    else:
        logger.error(f"Invalid target type: {target_type}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid target type: {target_type}",
        )


@router.get("/cat_target/{target}")
def cat_target(target: str, file: str, options: str = "", session: FLIP_Session = Depends(get_session)) -> str:
    """
    Runs the cat command on a file of the specified target.

    Args:
        target (str): target (e.g. site-1)
        file (str): name of the file to run cat on. Add relative path if needed.
        options (str, optional): extra arguments to cat.

    Returns:
        str: the result of the cat command.
    """
    try:
        return session.cat_target(target, options=options, file=file)
    except Exception as e:
        logger.error(f"Error running cat command on target {target}, file {file}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error running cat command on target {target}, file {file}: {e}",
        )


@router.get("/get_connected_client_list")
def get_connected_client_list(session: FLIP_Session = Depends(get_session)):
    """
    List of the connected clients.

    Returns:
        List[ClientInfo]: a list of ClientInfo objects.
    """
    try:
        return session.get_connected_client_list()
    except Exception as e:
        logger.error(f"Error getting connected client list: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting connected client list: {e}",
        )


@router.get("/get_working_directory/{target}")
def get_working_directory(target: str, session: FLIP_Session = Depends(get_session)) -> str:
    """
    Returns the working directory of the specified target.

    Args:
        target (str): target (e.g. site-1).

    Returns:
        str: current working directory of the specified target.
    """
    try:
        return session.get_working_directory(target)
    except Exception as e:
        logger.error(f"Error getting working directory for target {target}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting working directory for target {target}: {e}",
        )


@router.get("/grep_target/{target}")
def grep_target(
    target: str,
    options: str,
    pattern: str,
    file: str,
    session: FLIP_Session = Depends(get_session),
) -> str:
    """
    Runs the grep command on a file in the specified target.

    Args:
        target (str): name of target
        options (str): options for the grep command. Note that only -n and -i are supported.
        pattern (str): pattern to search for.
        file (str): file name where search is performed. Only exact files are supported (e.g. no wildcards * ).
        session (FLIP_Session): the FLIP session instance.

    Returns:
        str: the output of the grep command.

    Raises:
        HTTPException: if there is an error during the grep process.
    """
    try:
        return session.grep_target(
            target,
            options=options,
            pattern=pattern,
            file=file,
        )
    except Exception as e:
        logger.error(f"Error running grep command on target {target}, file {file}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error running grep command on target {target}, file {file}: {e}",
        )


@router.post("/restart/{target_type}")
@router.post("/restart/{target_type}/{targets}")
def restart(target_type: TargetType, targets: Optional[str] = None, session: FLIP_Session = Depends(get_session)):
    """
    Restart specified system target(s). [CAREFUL]: restarting the server might cause the session to drop. You'll
    need to re-start the API.

    Args:
        target_type (TargetType): type of target to restart. Can be server, client or all.
        targets (Optional[str]): if target_type is client, this is a comma-separated list of client names. If a target
        is not in the actual list of clients, it will be ignored.
        session (FLIP_Session): the FLIP session instance.

    Returns:
        dict: contains detailed info about the restart request:
            status - the overall status of the result.
            server_status - whether the server is restarted successfully - only if target_type is "all" or "server".
            client_status - a dict (keyed on client name) that specifies status of each client - only if target_type
                is "all" or "client".
    """
    try:
        return session.restart(target_type, (targets.split(",") if targets else targets))
    except Exception as e:
        logger.error(f"Error restarting {target_type} with targets {targets}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error restarting {target_type} with targets {targets}: {e}",
        )


@router.post("/set_timeout/{timeout}")
def set_timeout(timeout: float, session: FLIP_Session = Depends(get_session)):
    """
    Set a session-specific command timeout.

    This is the amount of time the server will wait for responses after sending commands to FL clients.

    Note that this value is only effective for the current API session.

    Args:
        timeout (float): a positive float number for the timeout in seconds.
        session (FLIP_Session): the FLIP session instance.

    Returns:
        None

    """
    try:
        return session.set_timeout(timeout)
    except Exception as e:
        logger.error(f"Error setting timeout to {timeout}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error setting timeout to {timeout}: {e}",
        )


@router.get("/tail_target_log/{target}")
def tail_target_log(target: str, options: Optional[str] = None, session: FLIP_Session = Depends(get_session)):
    """
    Run the "tail log.txt" command on the specified target and return the result.

    Args:
        target: the target (server or a client name) the command will be run on
        options: options of the "tail" command
        session (FLIP_Session): the FLIP session instance.

    Returns:
        str: the output of the tail command.

    Raises:
        HTTPException: if there is an error during the tailing process.
    """
    try:
        return session.tail_target_log(target, options=options)
    except Exception as e:
        logger.error(f"Error tailing log for target {target}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error tailing log for target {target}: {e}",
        )


@router.get("/wait_until_server_status")
def wait_until_server_status(
    interval: int = 10,
    timeout: Optional[int] = None,
    fail_attempts: int = 3,
    session: FLIP_Session = Depends(get_session),
):
    """
    Function borrowed from the old FLAdminAPI (fl_admin_api.py). Checks the server status at regular intervals.
    If the status check succeeds, we call the callback function and return success. Otherwise, continue polling.
    If the status check does not succeed, we increment the number of failed attempts, in the end returning error.

    Args:
        interval (int, optional): Time between checks. Defaults to 20.
        timeout (int, optional): Maximum waiting time. Defaults to None.
        callback (Callable[[FLAdminAPIResponse, Optional[List]], bool], optional): Status check function. Defaults to
        default_server_status_handling_cb.
        fail_attempts (int, optional): Maximum consecutive failures allowed. Defaults to 3.
        session (FLIP_Session): the FLIP session instance.

    Returns:
        FLAdminAPIResponse: _description_
    """
    try:
        return session.wait_until_server_status(
            interval=interval,
            timeout=timeout,
            fail_attempts=fail_attempts,
        )
    except Exception as e:
        logger.error(f"Error waiting for server status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error waiting for server status: {e}",
        )


@router.post("/shutdown/{target_type}")
@router.post("/shutdown/{target_type}/{targets}")
def shutdown(
    target_type: TargetType,
    targets: Optional[str] = None,
    session: FLIP_Session = Depends(get_session),
) -> None:
    """
    Shut down specific services that are part of the FL system.

    Args:
        target_type (TargetType): type of target. Either server, client or all.
        targets (Optional[str], optional): for target_type client, this is a list of specific clients you want to shut
        down. If not specified, all clients will be shut down.
        session (FLIP_Session): the FLIP session instance.

    Returns:
        None

    Raises:
        HTTPException: if there is an error during the shutdown process.
    """
    try:
        return session.shutdown(target_type, (targets.split(",") if targets else targets))
    except Exception as e:
        logger.error(f"Error shutting down {target_type} {targets}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error shutting down {target_type} {targets}: {e}",
        )


@router.post("/shutdown_system")
def shutdown_system(session: FLIP_Session = Depends(get_session)) -> None:
    """
    Shuts down the whole FL system.

    Args:
        session (FLIP_Session): the FLIP session instance.

    Returns:
        None

    Raises:
        HTTPException: if there is an error during the shutdown process.
    """
    try:
        return session.shutdown_system()
    except Exception as e:
        logger.error(f"Error shutting down system: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error shutting down system: {e}",
        )
