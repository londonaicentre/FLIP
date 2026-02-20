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

import time
from enum import Enum
from typing import List

from pydantic import BaseModel


class FLAggregators(Enum):
    """Enumeration for different FL aggregators"""

    IN_TIME_ACCUMULATE_WEIGHTED_AGGREGATOR = "InTimeAccumulateWeightedAggregator"
    ACCUMULATE_WEIGHTED_AGGREGATOR = "AccumulateWeightedAggregator"


class UploadAppRequest(BaseModel):
    """
    Defines the body of the request to upload an application to the server.

    See full list of aggregators in https://nvflare.readthedocs.io/en/2.7.1/apidocs/nvflare.app_common.aggregators.html
    """

    project_id: str
    cohort_query: str
    local_rounds: int
    global_rounds: int
    trusts: List[str]
    bundle_urls: List[str]
    ignore_result_error: bool = False
    aggregator: str = FLAggregators.IN_TIME_ACCUMULATE_WEIGHTED_AGGREGATOR.value
    aggregation_weights: dict = {}


class ServerInfoModel(BaseModel):
    """Pydantic model for server status information. Based on FLARE ServerInfo class."""

    status: str
    start_time: float

    def __str__(self) -> str:
        return f"status: {self.status}, start_time: {time.asctime(time.localtime(self.start_time))}"


class ClientInfoModel(BaseModel):
    """Pydantic model for client status information. Extends FLARE ClientInfo class to include client status."""

    name: str
    last_connect_time: float
    status: str

    def __str__(self) -> str:
        return f"""
        {self.name}(last_connect_time: {time.asctime(time.localtime(self.last_connect_time))}, status: {self.status})
        """


class JobInfoModel(BaseModel):
    """Pydantic model for job information. Based on FLARE JobInfo class."""

    job_id: str
    app_name: str

    def __str__(self) -> str:
        return f"JobInfo:\n  job_id: {self.job_id}\n  app_name: {self.app_name}"


class SystemInfoModel(BaseModel):
    """Pydantic model for system information. Combines server info, client info, and job info into a single model."""

    server_info: ServerInfoModel
    client_info: List[ClientInfoModel]
    job_info: List[JobInfoModel]

    def __str__(self) -> str:
        client_info_str = "\n".join(map(str, self.client_info))
        job_info_str = "\n".join(map(str, self.job_info))
        return (
            f"SystemInfo\nserver_info:\n{self.server_info}\nclient_info:\n{client_info_str}\njob_info:\n{job_info_str}"
        )
