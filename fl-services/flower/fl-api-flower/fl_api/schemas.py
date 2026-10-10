# Copyright (c) 2026 Flower Labs GmbH
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

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from fl_api.utils.logger import logger


class HealthResponse(BaseModel):
    status: str
    version: str


class FlowerCommandResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class FlowerSubmitRunCommandResponse(FlowerCommandResponse):
    run_id: str = Field(alias="run-id")


class ServerInfoModel(BaseModel):
    status: str


class ClientInfoModel(BaseModel):
    name: str
    status: str


class ErrorResponse(BaseModel):
    detail: str


class NodeRegistrationRequest(BaseModel):
    """Request body for SuperNode self-registration with the FL API."""

    name: str
    node_id: str


# Sanity caps on a job's GPU request — the same contract as the NVFLARE fl-api's (FLIP#70).
MAX_GPUS_PER_SITE = 8
MAX_MEM_PER_GPU_GIB = 192


class JobResources(BaseModel):
    """What a job needs at each participating site — the hub contract shared with the NVFLARE fl-api (FLIP#70).

    Flower's deployment runtime (SuperLink and SuperNodes) places no job by GPU — ``num-gpus`` is a
    simulation-backend option only — so this adapter records the request and reports it as not enforced
    rather than pretending to schedule by it.
    """

    model_config = ConfigDict(extra="forbid")

    num_gpus: StrictInt = Field(ge=0, le=MAX_GPUS_PER_SITE)
    mem_per_gpu_gib: StrictInt = Field(default=0, ge=0, le=MAX_MEM_PER_GPU_GIB)

    @model_validator(mode="after")
    def _memory_needs_gpus(self) -> "JobResources":
        if self.mem_per_gpu_gib and not self.num_gpus:
            raise ValueError("mem_per_gpu_gib needs num_gpus > 0: memory per GPU means nothing without a GPU")
        return self


class UploadAppRequest(BaseModel):
    """Defines the body of the request to upload an application to the server."""

    project_id: str
    cohort_query: str
    trusts: list[str]
    bundle_urls: list[str]
    # The researcher's GPU override at submission, if any (FLIP#70). Recorded, not enforced, on Flower.
    resources: JobResources | None = None


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


class JobMetadata(BaseModel):
    """A single item of ``GET /list_jobs`` — the shared job-metadata contract (FLIP issue #490)."""

    job_id: str
    status: JobStatus


# Flower native status (`flwr list` / `flwr stop`) -> normalized contract status.
# Note: `flwr list` reports terminal states as `finished:*`, while `flwr stop` reports
# the bare form `stopped` — both are mapped here.
_FLOWER_STATUS_MAP: dict[str, JobStatus] = {
    "pending": JobStatus.PENDING,
    "starting": JobStatus.PENDING,
    "running": JobStatus.RUNNING,
    "finished:completed": JobStatus.FINISHED,
    "finished:failed": JobStatus.FAILED,
    "finished:stopped": JobStatus.STOPPED,
    "stopped": JobStatus.STOPPED,
}


def normalize_status(native_status: str) -> JobStatus:
    """Map a Flower native run status to the normalized ``JobStatus`` contract value.

    Args:
        native_status (str): The raw status string from ``flwr list`` / ``flwr stop``.

    Returns:
        JobStatus: The normalized status. Unknown / unmapped statuses are logged and
            treated as ``FAILED`` — never silently surfaced as an abortable ``RUNNING``.
    """
    normalized = _FLOWER_STATUS_MAP.get(native_status.strip().lower())
    if normalized is None:
        logger.warning("Unmapped Flower job status %r; treating as FAILED.", native_status)
        return JobStatus.FAILED
    return normalized
