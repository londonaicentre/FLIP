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
from enum import Enum, IntEnum, StrEnum

from pydantic import BaseModel

from fl_api.utils.logger import logger


class UploadAppRequest(BaseModel):
    """
    Defines the body of the request to upload an application to the server.
    """

    project_id: str
    cohort_query: str
    trusts: list[str]
    bundle_urls: list[str]


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
    client_info: list[ClientInfoModel]
    job_info: list[JobInfoModel]

    def __str__(self) -> str:
        client_info_str = "\n".join(map(str, self.client_info))
        job_info_str = "\n".join(map(str, self.job_info))
        return (
            f"SystemInfo\nserver_info:\n{self.server_info}\nclient_info:\n{client_info_str}\njob_info:\n{job_info_str}"
        )


class FLAggregators(Enum):
    """Enumeration for different FL aggregators"""

    InTimeAccumulateWeightedAggregator = "InTimeAccumulateWeightedAggregator"
    AccumulateWeightedAggregator = "AccumulateWeightedAggregator"


class AggregationWeights:
    MinimumAggregationWeight = 0
    MaximumAggregationWeight = 1


# TODO Decide if we want to keep this or not
# The original value of MAX was 100, but this was increased to 1000 due to
# https://github.com/londonaicentre/flipe-application/pull/47, where 'global round' can be something else, e.g.
# a combined value of global round and local round.
class TrainingRound(IntEnum):
    MIN = 1
    MAX = 1000


class TargetType(StrEnum):
    """System target for admin operations (restart/shutdown), used as a FastAPI path param.

    NVFLARE 2.8.0 removed the deprecated ``nvflare.fuel.hci.client.fl_admin_api`` tree, which
    previously exported ``TargetType`` as a ``(str, Enum)``. Its successor,
    ``nvflare.fuel.flare_api.api_spec.TargetType``, is a plain class (bare string class-attrs) that
    FastAPI rejects as a path-param annotation. We therefore re-declare it locally as a ``StrEnum``,
    preserving the original wire contract: the member values are exactly the ``"all"``/``"server"``/
    ``"client"`` strings that ``flare_api.Session.restart``/``shutdown`` validate against.
    """

    ALL = "all"
    SERVER = "server"
    CLIENT = "client"


class IOverridableConfig(BaseModel):
    LOCAL_ROUNDS: int | None = None
    GLOBAL_ROUNDS: int | None = None
    IGNORE_RESULT_ERROR: bool | None = None
    AGGREGATOR: str | None = None
    AGGREGATION_WEIGHTS: dict[str, float] | None = None
    # Regex of model-parameter names to KEEP in each per-round client update. When set, a
    # KeepOnlyVars task_result_filter is injected client-side so only matching (trainable) params
    # are sent — e.g. a frozen-backbone fine-tune sends just its head, avoiding the large-payload
    # uplink timeout (FLIP#684). Unset (default) = full-model update, unchanged behaviour.
    AGGREGATE_ONLY_REGEX: str | None = None
    # Validation-metric label driving best-global-model selection. When set, a stock
    # IntimeModelSelector is injected server-side so the best global model is saved alongside the
    # final one (FLIP#673); the client trainer must report this metric on its returned FLModel.
    # Unset (default) = no selector, only the final model is saved.
    BEST_MODEL_METRIC: str | None = None
    # Whether lower metric values are better (loss-like). Negates the selector's key metric.
    BEST_MODEL_METRIC_MINIMIZE: bool = False


class JobStatus(StrEnum):
    """Normalized FL-backend job lifecycle status — the shared job-metadata contract
    (FLIP issue #490). Every FL-API adapter maps its native runtime status into one of
    these values; flip-api's ``IJobMetaData`` consumes only these.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    # A native status the map below does not recognise — i.e. a framework upgrade added one.
    # Never guessed into a real state: FAILED now has destructive consumers on the hub (the
    # failed-job reconcile errors the model and frees the net), and RUNNING would make the
    # job abortable, so an unmapped status must reach the hub as explicitly unknowable.
    UNKNOWN = "UNKNOWN"


class JobMetadata(BaseModel):
    """A single item of ``GET /list_jobs`` — the shared job-metadata contract (FLIP issue #490)."""

    job_id: str
    status: JobStatus


# NVFLARE RunStatus (nvflare/apis/job_def.py) -> normalized contract status.
_NVFLARE_STATUS_MAP: dict[str, JobStatus] = {
    "SUBMITTED": JobStatus.PENDING,
    "APPROVED": JobStatus.PENDING,
    "DISPATCHED": JobStatus.PENDING,
    "RUNNING": JobStatus.RUNNING,
    "FINISHED:COMPLETED": JobStatus.FINISHED,
    "FINISHED:ABORTED": JobStatus.STOPPED,
    "FINISHED:EXECUTION_EXCEPTION": JobStatus.FAILED,
    "FINISHED:ABNORMAL": JobStatus.FAILED,
    "FINISHED:CAN_NOT_SCHEDULE": JobStatus.FAILED,
    "FINISHED:FAILED_TO_RUN": JobStatus.FAILED,
    "FINISHED:ABANDONED": JobStatus.FAILED,
}


def normalize_status(native_status: str) -> JobStatus:
    """Map an NVFLARE ``RunStatus`` string to the normalized ``JobStatus`` contract value.

    Args:
        native_status (str): The raw status string from ``session.list_jobs()``.

    Returns:
        JobStatus: The normalized status. Unknown / unmapped statuses are logged and
            surfaced as ``UNKNOWN`` — never guessed into an abortable ``RUNNING`` or an
            actionable ``FAILED`` (the hub's failed-job reconcile acts on FAILED).
    """
    normalized = _NVFLARE_STATUS_MAP.get(native_status.strip().upper())
    if normalized is None:
        logger.warning("Unmapped NVFLARE job status %r; surfacing as UNKNOWN.", native_status)
        return JobStatus.UNKNOWN
    return normalized
