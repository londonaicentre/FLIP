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

import re
import time
from typing import Callable, List, Optional, Union

from nvflare.apis.fl_constant import AdminCommandNames
from nvflare.apis.utils.format_check import type_pattern_mapping
from nvflare.fuel.flare_api.api_spec import (
    InternalError,
    JobNotFound,
    ServerInfo,
)
from nvflare.fuel.flare_api.flare_api import Session
from nvflare.fuel.hci.client.api import ResultKey
from nvflare.fuel.hci.client.api_status import APIStatus
from nvflare.fuel.hci.client.fl_admin_api import (
    TargetType,
)
from nvflare.fuel.hci.client.fl_admin_api_spec import APISyntaxError, FLAdminAPIResponse
from nvflare.fuel.hci.cmd_arg_utils import validate_text_file_name
from nvflare.fuel.hci.proto import MetaKey

from fl_api.utils.logger import logger
from fl_api.utils.schemas import ClientInfoModel

# Auxiliary functions


def default_server_status_handling_cb(reply) -> bool:
    # Borrowed from old fl_admin_api
    if reply.get(MetaKey.SERVER_STATUS) == "stopped":
        return True
    else:
        return False


def new_secure_Flip_session(
    username: str, startup_kit_location: str, debug: bool = False, timeout: float = 20.0
) -> "FLIP_Session":
    """
    Create a new secure FLARE API Flip with the NVFLARE system.

    Args:
        username (str): username assigned to the user
        startup_kit_location (str): path to the provisioned startup folder, the root admin dir containing the startup
        folder.
        debug (bool): enable debug mode
        timeout (float): how long to try to establish the session, in seconds

    Returns:
        FLIP_Session: a FLIP_Session instance connected to the NVFLARE system.
    """
    session = FLIP_Session(
        username=username,
        startup_path=startup_kit_location,
        secure_mode=False,
        debug=debug,
    )
    session.try_connect(timeout)
    return session


class FLIP_Session(Session):
    def __init__(
        self,
        username: Union[str, None] = None,
        startup_path: Union[str, None] = None,
        secure_mode: bool = False,
        debug: bool = False,
    ):
        super(FLIP_Session, self).__init__(username, startup_path, secure_mode, debug)
        self._error_buffer = None

    def _do_command(self, cmd: str):
        """Override the _do_command method to add error handling for session inactivity. If a session_inactive error is
        caught, the method will attempt to reconnect and retry the command once."""
        try:
            return super()._do_command(cmd)
        except InternalError as e:
            if "session_inactive" in str(e):
                logger.warning("Session inactive, trying to reconnect...")
                self.try_connect(timeout=5.0)
                return super()._do_command(cmd)
            raise e

    def _validate_required_target_string(self, target: str) -> str:
        """
        Returns the target if it exists and it is valid.

        Args:
            target (str): name of the target

        Returns:
            str: target name if it exists and is valid, otherwise error.

        Raises:
            APISyntaxError: if the target is not valid or does not exist.
        """
        if not target:
            raise APISyntaxError("target is required but not specified.")
        if not isinstance(target, str):
            raise APISyntaxError("target is not str.")
        if not re.match("^[A-Za-z0-9._-]*$", target):
            raise APISyntaxError("target must be a string of only valid characters and no spaces.")
        return target

    def _validate_file_string(self, file: str) -> str:
        """Returns the file string if it exists and is valid.

        Args:
            file (str): file name

        Returns:
            str: file name if it exists and is valid, otherwise error.

        Raises:
            APISyntaxError: returns an error if the file name does not exist or is invalid.
        """
        if not isinstance(file, str):
            raise APISyntaxError("file is not str.")
        if not re.match("^[A-Za-z0-9-._/]*$", file):
            raise APISyntaxError("unsupported characters in file {}".format(file))
        if file.startswith("/"):
            raise APISyntaxError("absolute path for file is not allowed")
        paths = file.split("/")
        for p in paths:
            if p == "..":
                raise APISyntaxError(".. in file path is not allowed")

        err = validate_text_file_name(file)
        if err:
            raise APISyntaxError(err)
        return file

    def _validate_sp_string(self, sp_string) -> str:
        """Returns the sp_string if it is valid."""
        if re.match(
            type_pattern_mapping.get("sp_end_point"),
            sp_string,
        ):
            return sp_string
        else:
            raise APISyntaxError("sp_string must be of the format example.com:8002:8003")

    def check_server_status(self) -> ServerInfo:
        """
        Checks the status of the server.

        NOTE that this API considers one server only. For multiple servers systems,
        this function should accommodate a list of servers as argument, similar to how the client status is handled.

        Returns:
            ServerInfo: a ServerInfo object containing the server status and start time.
        """
        server_info = self.get_system_info().server_info
        return ServerInfo(server_info.status, server_info.start_time)

    def check_client_status(self, target: Optional[List[str]] = None) -> List[ClientInfoModel]:
        """
        Check status of every client or specific clients.

        Args:
            target (List[str]): list of client names to check status for. If empty, all clients will be returned.

        Returns:
            List[fl_api.utils.schemas.ClientInfoModel]: a list of ClientInfoModel objects containing name, last connect
            time, and status.
        """
        system_info = self.get_system_info()
        if target:
            clients_info = [client for client in system_info.client_info if client.name in target]
        else:
            clients_info = system_info.client_info

        # Convert to ClientInfoModel objects
        clients = [
            ClientInfoModel(name=c.name, last_connect_time=c.last_connect_time, status="not set") for c in clients_info
        ]

        # Also get client status
        for client in clients:
            client_job_status = self.get_client_job_status([client.name])
            assert len(client_job_status) == 1, "Expected only one job status for client {}".format(client.name)
            client_job_status = client_job_status[0]
            logger.info(f"client_job_status: {client_job_status}")
            client.status = client_job_status.get("status", "unknown")

        return clients

    def download_job_result(self, job_id: str) -> str:
        """Download result of the job.

        Args:
            job_id (str): ID of the job

        Raises:
            JobNotFound: if the job ID is not found.

        Returns:
            str: If the job size is smaller than the maximum size set on the server, the job will download to the
            download_dir set in Session through the admin config, and the path to the downloaded result will be
            returned. If the size of the job is larger than the maximum size, the location to download the job will be
            returned.
        """
        try:
            self._validate_job_id(job_id)
        except JobNotFound as jnf_ex:
            raise jnf_ex
        result = self._do_command(AdminCommandNames.DOWNLOAD_JOB + " " + job_id)
        meta = result[ResultKey.META]
        location = meta.get(MetaKey.LOCATION)
        return location

    def reset_errors(self, job_id: str) -> None:
        """Clear errors for all system targets for the specified job, if job exists.

        Args:
            job_id (str): ID of the job
        Raises:
            JobNotFound: if the job ID is not found.

        Returns:
            None
        """
        try:
            self._validate_job_id(job_id)
        except JobNotFound as jnf_ex:
            raise jnf_ex

        self._collect_info(AdminCommandNames.RESET_ERRORS, job_id, TargetType.ALL)

    def abort_job(self, job_id: str) -> None:
        """Abort the specified job.

        Args:
            job_id (str): ID of the job.

        Returns:
            None

        If the job is already done, no effect;
        If job is not started yet, it will be cancelled and won't be scheduled.
        If the job is being executed, it will be aborted.
        """
        try:
            self._validate_job_id(job_id)
        except JobNotFound as jnf_ex:
            raise jnf_ex

        self._do_command(AdminCommandNames.ABORT_JOB + " " + job_id)

    def delete_job(self, job_id: str) -> None:
        """Delete the specified job completely from the system.

        Args:
            job_id (str): ID of the job.

        Returns:
            None

        The job will be deleted from the job store if the job is not currently running.
        """
        try:
            self._validate_job_id(job_id)
        except JobNotFound as jnf_ex:
            raise jnf_ex

        self._do_command(AdminCommandNames.DELETE_JOB + " " + job_id)

    def wait_until_server_status(
        self,
        interval: int = 20,
        timeout: Union[int, None] = None,
        callback: Callable[[FLAdminAPIResponse], bool] = default_server_status_handling_cb,
        fail_attempts: int = 3,
        **kwargs,
    ) -> FLAdminAPIResponse:
        """Function borrowed from the old FLAdminAPI (fl_admin_api.py). Checks the server status at regular intervals.
        If the status check succeeds, we call the callback function and return success. Otherwise, continue polling.
        If the status check does not succeed, we increment the number of failed attempts, in the end returning error.

        Args:
            interval (int, optional): Time between checks. Defaults to 20.
            timeout (int, optional): Maximum waiting time. Defaults to None.
            callback (Callable[[FLAdminAPIResponse, Optional[List]], bool], optional): Status check function. Defaults
            to default_server_status_handling_cb.
            fail_attempts (int, optional): Maximum consecutive failures allowed. Defaults to 3.

        Returns:
            FLAdminAPIResponse: _description_
        """
        failed_attempts = 0
        start = time.time()
        while True:
            try:
                reply = self.check_server_status()
            except Exception as e:
                return FLAdminAPIResponse(
                    APIStatus.ERROR_RUNTIME,
                    {"message": f"Failed to check server status: {str(e)}"},
                    None,
                )

            if reply.status:
                met = callback(reply, **kwargs)
                if met:
                    return FLAdminAPIResponse(APIStatus.SUCCESS, {}, None)
                failed_attempts = 0
            else:
                print("Could not get reply from check status server, trying again later")
                failed_attempts += 1

            now = time.time()
            if timeout is not None:
                if now - start >= timeout:
                    return FLAdminAPIResponse(APIStatus.SUCCESS, {"message": "Waited until timeout."}, None)
            if failed_attempts > fail_attempts:
                return FLAdminAPIResponse(
                    APIStatus.ERROR_RUNTIME,
                    {
                        "message": "FL server status was not obtainable for more than the specified number of "
                        "fail_attempts. "
                    },
                    None,
                )
            time.sleep(interval)
