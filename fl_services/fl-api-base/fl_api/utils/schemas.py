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


class ClientInfoModel(BaseModel):
    """Extends the ClientInfo class to include client status."""

    name: str
    last_connect_time: float
    status: str

    def __str__(self) -> str:
        return f"""
        {self.name}(last_connect_time: {time.asctime(time.localtime(self.last_connect_time))}, status: {self.status})
        """
