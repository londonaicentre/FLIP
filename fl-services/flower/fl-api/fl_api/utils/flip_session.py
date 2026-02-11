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

from typing import List, Optional

from fl_api.utils.schemas import ClientInfoModel, JobMetaData, ServerInfoModel


class FLIP_Session:
    def __init__(
        self,
        upload_dir: str,
        secure_mode: bool = False,
        debug: bool = False,
    ):
        self.upload_dir = upload_dir
        self.secure_mode = secure_mode
        self.debug = debug

    def try_connect(self, timeout: float) -> bool:
        """
        Attempt to connect to the server.

        Args:
            timeout (float): The maximum time to wait for a connection, in seconds.

        Returns:
            bool: True if connection is successful, False otherwise.
        """
        return NotImplementedError("try_connect method is not implemented yet.")  # type: ignore

    def submit_job(self, job_folder: str) -> str:
        """
        Submit a job to the server.

        Args:
            job_folder (str): The folder where the job is located.

        Returns:
            str: job ID if the system accepts the job.
        """
        # TODO add logic to submit a job to the server using the job_folder
        mock_job_id = "59ea671e-6551-49cf-a81b-0c5011a7401b"
        return mock_job_id

    def list_jobs(self) -> List[JobMetaData]:
        """
        List all jobs on the server.

        Returns:
            List[JobMetaData]: a list of job meta data.
        """
        # TODO add logic to list all jobs on the server
        # We only really care about job_id and status - Central Hub will check status == "RUNNING"
        mock_job_data = [
            {
                "job_id": "59ea671e-6551-49cf-a81b-0c5011a7401b",
                "job_name": "665dcd35-7dbf-4ad1-93dc-1773a7b44de1",
                "status": "RUNNING",
                "submit_time": "2025-10-08T14:25:47.119547+00:00",
                "duration": "2:38:26.597705",
            },
        ]
        job_data = [JobMetaData.model_validate(job) for job in mock_job_data]
        return job_data

    def abort_job(self, job_id: str) -> str:
        """
        Abort a job with the given job ID.

        Args:
            job_id (str): The ID of the job to abort.

        Returns:
            str: the message from the server
        """
        # TODO add logic to abort the job with the given job_id
        # e.g. flwr stop ... --job_id {job_id}
        return f"Job {job_id} aborted."

    def check_server_status(self) -> ServerInfoModel:
        """
        Checks the status of the server.

        Returns:
            ServerInfoModel: a ServerInfoModel object containing the server status and start time.
        """
        # TODO add logic to check the status of the server and return a ServerInfoModel object
        mock_server_info = ServerInfoModel(status="RUNNING", start_time=1701000000.0)
        return mock_server_info

    def check_client_status(self, target: Optional[List[str]] = None) -> List[ClientInfoModel]:
        """
        Check status of every client or specific clients.

        Args:
            target (List[str]): list of client names to check status for. If empty, all clients will be returned.

        Returns:
            List[fl_api.utils.schemas.ClientInfoModel]: a list of ClientInfoModel objects containing name, last connect
            time, and status.
        """
        # TODO add logic to check the status of every client or specific clients and return a list of ClientInfoModel
        # objects
        mock_client_info = [
            ClientInfoModel(name="client1", last_connect_time=1701000000.0, status="CONNECTED"),
            ClientInfoModel(name="client2", last_connect_time=1701000000.0, status="DISCONNECTED"),
        ]
        return mock_client_info
