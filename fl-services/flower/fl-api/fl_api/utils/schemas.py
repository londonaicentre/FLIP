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

import time
from typing import List

from pydantic import BaseModel, ConfigDict


class UploadAppRequest(BaseModel):
    """
    Defines the body of the request to upload an application to the server.
    """

    model_config = ConfigDict(extra="ignore")

    project_id: str
    cohort_query: str
    trusts: List[str]
    bundle_urls: List[str]


class JobMetaData(BaseModel):
    """
    Defines the meta data of a job.
    """

    model_config = ConfigDict(extra="ignore")

    job_id: str
    job_name: str
    status: str


class ServerInfoModel(BaseModel):
    """Pydantic model for server status information."""

    status: str
    start_time: float

    def __str__(self) -> str:
        return f"status: {self.status}, start_time: {time.asctime(time.localtime(self.start_time))}"


class ClientInfoModel(BaseModel):
    """Pydantic model for client status information."""

    name: str
    last_connect_time: float
    status: str

    def __str__(self) -> str:
        return f"""
        {self.name}(last_connect_time: {time.asctime(time.localtime(self.last_connect_time))}, status: {self.status})
        """
