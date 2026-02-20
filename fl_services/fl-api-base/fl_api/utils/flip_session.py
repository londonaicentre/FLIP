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

from typing import List, Optional, Union

from nvflare.fuel.flare_api.api_spec import InternalError
from nvflare.fuel.flare_api.flare_api import Session

from fl_api.utils.logger import logger
from fl_api.utils.schemas import ClientInfoModel, JobInfoModel, ServerInfoModel, SystemInfoModel


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
        """
        Override the _do_command method to add error handling for session inactivity. If a session_inactive error is
        caught, the method will attempt to reconnect and retry the command once.

        Args:
            cmd (str): The command to be executed.
        """
        try:
            return super()._do_command(cmd)
        except InternalError as e:
            if "session_inactive" in str(e):
                logger.warning("Session inactive, trying to reconnect...")
                self.try_connect(timeout=5.0)
                return super()._do_command(cmd)
            raise e

    def check_server_status(self) -> ServerInfoModel:
        """
        Checks the status of the server.

        NOTE that this API considers one server only. For multiple servers systems, this function should accommodate a
        list of servers as argument, similar to how the client status is handled.

        Returns:
            ServerInfoModel: a ServerInfoModel object containing the server status and start time.
        """
        return self.get_system_info().server_info

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

        # Convert response to ClientInfoModel objects
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

    def get_system_info(self) -> SystemInfoModel:
        """
        Get system info of the FL system.

        Returns:
            SystemInfoModel: system info of the FL system.
        """
        info = super().get_system_info()
        system_info = SystemInfoModel(
            server_info=ServerInfoModel(status=info.server_info.status, start_time=info.server_info.start_time),
            client_info=[
                ClientInfoModel(name=c.name, last_connect_time=c.last_connect_time, status="not set")
                for c in info.client_info
            ],
            job_info=[JobInfoModel(job_id=j.job_id, app_name=j.app_name) for j in info.job_info],
        )
        return system_info

    def get_connected_client_list(self) -> List[ClientInfoModel]:
        """
        Get a list of the connected clients.

        Returns:
            List[ClientInfoModel]: a list of ClientInfoModel objects containing name, last connect time, and status.
        """
        return self.get_system_info().client_info
