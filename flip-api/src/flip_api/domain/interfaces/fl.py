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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flip_api.domain.interfaces.shared import TrainingRound

# Path to the JSON file containing job types and required files (relative to this file)
REQUIRED_JOB_TYPES_FILE = Path(__file__).parent.parent.parent / "assets" / "required_job_types.json"


def _load_job_types_config() -> Dict[str, List[str]]:
    """Loads the job types configuration from the JSON file.

    Returns:
        Dict[str, List[str]]: A dictionary mapping job type names to their required files.

    Raises:
        FileNotFoundError: If the required_job_types.json file is not found.
        json.JSONDecodeError: If the JSON file is malformed.
    """
    try:
        with open(REQUIRED_JOB_TYPES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        # Log error and return empty dict so the API doesn't crash
        import sys

        print(f"[ERROR] Could not load required_job_types.json: {e}", file=sys.stderr)
        return {}


# Load job types configuration at module level
_JOB_TYPES_CONFIG = _load_job_types_config()


class IStartTrainingBody(BaseModel):
    project_id: str
    cohort_query: str
    local_rounds: int
    global_rounds: int
    trusts: List[str]
    bundle_urls: List[str]
    ignore_result_error: Optional[bool] = False
    aggregator: Optional[str] = None
    aggregation_weights: Optional[Dict[str, float]] = None
    job_type: Optional[str] = "standard"


class ISchedulerResponse(BaseModel):
    id: UUID
    netId: UUID


class IJobResponse(BaseModel):
    id: UUID  # FLJob table primary key
    model_id: UUID
    clients: List[str]


class IJobMetaData(BaseModel):
    """
    Defines the meta data of a job.
    """

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

    @model_validator(mode="after")
    def validate_trusts_min_length(self):
        if len(self.trusts) < TrainingRound.MIN:
            raise ValueError(f"'trusts' must contain at least {TrainingRound.MIN} string(s)")
        return self


class INetDetails(BaseModel):
    name: str
    endpoint: str


class IServerStatus(BaseModel):
    status: str
    start_time: float


class IClientStatus(BaseModel):
    name: str
    online: bool
    status: str
    last_connected: Optional[float] = None


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
    def get_required_files(cls, job_type: Enum) -> List[str]:
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
