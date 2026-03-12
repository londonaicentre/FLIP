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

import json
from enum import Enum
from pathlib import Path
from typing import Annotated, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from flip_api.domain.schemas.status import ClientStatus
from flip_api.utils.constants import JOB_TYPES_REQUIRED_FILES_FILE

# Path to the JSON file containing job types and required files (relative to this file)
REQUIRED_JOB_TYPES_FILE = Path(__file__).parent.parent.parent / "assets" / JOB_TYPES_REQUIRED_FILES_FILE


def _load_job_types_config() -> Dict[str, List[str]]:
    """Loads the job types configuration from the JSON file.

    Returns:
        Dict[str, List[str]]: A dictionary mapping job type names to their required files.

    Raises:
        FileNotFoundError: If the file is not found.
        json.JSONDecodeError: If the JSON file is malformed.
    """
    try:
        with open(REQUIRED_JOB_TYPES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        # Log error and return empty dict so the API doesn't crash
        import sys

        print(f"[ERROR] Could not load {JOB_TYPES_REQUIRED_FILES_FILE}: {e}", file=sys.stderr)
        return {}


# Load job types configuration at module level
_JOB_TYPES_CONFIG = _load_job_types_config()


class IStartTrainingBody(BaseModel):
    project_id: str
    cohort_query: str
    trusts: List[str]
    bundle_urls: List[str]


class ISchedulerResponse(BaseModel):
    id: UUID
    netId: UUID


class IJobResponse(BaseModel):
    id: UUID  # FLJob table primary key
    model_id: UUID
    clients: List[str]


class IJobMetaData(BaseModel):
    """Defines the meta data of a job."""

    model_config = ConfigDict(extra="ignore")

    job_id: str
    job_name: str
    status: str


class IRequiredTrainingInformation(BaseModel):
    project_id: str
    cohort_query: str


class IInitiateTrainingInputPayload(BaseModel):
    trusts: List[Annotated[str, Field(strip_whitespace=True, min_length=1)]]

    @field_validator("trusts")
    @classmethod
    def must_be_unique(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("'trusts' must all be unique entries")
        return v


class INetDetails(BaseModel):
    name: str
    endpoint: str


class IServerStatus(BaseModel):
    """Defines the status of the server."""

    model_config = ConfigDict(extra="ignore")

    status: str


class IClientStatus(BaseModel):
    """Defines the status of a client."""

    model_config = ConfigDict(extra="ignore")

    name: str
    status: str

    # set the online status based on the client status
    # This property will be included in dumps / JSON schemas
    @computed_field  # type: ignore[prop-decorator]
    @property
    def online(self) -> bool:
        return self.status != ClientStatus.NO_REPLY.value


class INetStatus(BaseModel):
    name: str
    online: Optional[bool] = None
    registered_clients: Optional[int] = None
    net_in_use: Optional[bool] = None
    clients: List[IClientStatus]


class IOverridableConfig(BaseModel):
    LOCAL_ROUNDS: Optional[int] = None
    GLOBAL_ROUNDS: Optional[int] = None
    IGNORE_RESULT_ERROR: Optional[bool] = None
    AGGREGATOR: Optional[str] = None
    AGGREGATION_WEIGHTS: Optional[Dict[str, float]] = None


class FLAggregators(Enum):
    """Enumeration for different FL aggregators"""

    InTimeAccumulateWeightedAggregator = "InTimeAccumulateWeightedAggregator"
    AccumulateWeightedAggregator = "AccumulateWeightedAggregator"


class AggregationWeights:
    MinimumAggregationWeight = 0
    MaximumAggregationWeight = 1


# Dynamically create JobTypes enum from the JSON configuration
JobTypes = Enum("JobTypes", {job_type: job_type for job_type in _JOB_TYPES_CONFIG.keys()})  # type: ignore[misc]


class JobRequiredFiles(BaseModel):
    model_config = {"extra": "allow"}

    def __init__(self, **data):
        """Initialize with required files from JSON configuration."""
        super().__init__(**data)
        config = self._load_job_types_config()
        for job_type, files in config.items():
            setattr(self, job_type, files)

    @classmethod
    def get_required_files(cls, job_type: JobTypes) -> List[str]:
        """Returns the list of required files for a specific job type (always reloads from disk)."""
        config = _load_job_types_config()
        return config.get(job_type.value, [])

    @classmethod
    def get_all_job_types_with_files(cls) -> Dict[str, List[str]]:
        """Returns all job types with their required files (always reloads from disk)."""
        return _load_job_types_config().copy()

    @classmethod
    def get_job_type_names(cls) -> List[str]:
        """Returns a list of all valid job type names.

        Returns:
            List[str]: List of job type names.
        """
        return list(_JOB_TYPES_CONFIG.keys())
