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

"""
FLIP Constants and Configuration.

This module provides:
    - Environment-aware settings (DevSettings, ProdSettings)
    - Enumerations for resource types, model statuses, tasks, and events
"""

from enum import Enum
from typing import Union

from pydantic import HttpUrl, PositiveInt, field_validator
from pydantic_settings import BaseSettings


class _Common(BaseSettings):
    """Base settings shared by both development and production environments."""

    LOCAL_DEV: bool  # Must be explicitly set
    MIN_CLIENTS: PositiveInt = 1


class DevSettings(_Common):
    """Development environment configuration.

    Used when LOCAL_DEV=true. Requires local paths for test data.
    """

    LOCAL_DEV: bool = True

    DEV_DATAFRAME: str
    DEV_IMAGES_DIR: str


class ProdSettings(_Common):
    """Production environment configuration.

    Used when LOCAL_DEV=false. Requires API URLs and credentials.
    """

    LOCAL_DEV: bool = False

    CENTRAL_HUB_API_URL: HttpUrl
    DATA_ACCESS_API_URL: HttpUrl
    IMAGING_API_URL: HttpUrl
    IMAGES_DIR: str
    PRIVATE_API_KEY_HEADER: str
    PRIVATE_API_KEY: str
    NET_ID: str
    UPLOADED_FEDERATED_DATA_BUCKET: str

    @field_validator("UPLOADED_FEDERATED_DATA_BUCKET")
    @classmethod
    def _validate_s3_url(cls, v: str) -> str:
        if not v.startswith("s3://"):
            raise ValueError(f"Invalid S3 URL: {v}")
        return v


# Environment-aware singleton - instantiate once and import elsewhere
FlipConstants: Union[DevSettings, ProdSettings] = (
    DevSettings() if _Common().LOCAL_DEV else ProdSettings()  # type: ignore[call-arg]
)


class ResourceType(str, Enum):
    """Types of imaging resources available in XNAT."""

    DICOM = "DICOM"
    NIFTI = "NIFTI"
    SEGMENTATION = "SEG"
    ALL = "ALL"


class FlipTasks(str, Enum):
    """Task names used in FLIP workflows."""

    # Common tasks (all job types)
    INIT_TRAINING = "init_training"
    POST_VALIDATION = "post_validation"
    CLEANUP = "cleanup"

    # Evaluation-specific tasks
    INIT_TASK = "init_task"
    POST_TASK = "post_task"


class FlipEvents:
    """Event names used in FLIP workflows.

    Note: This is a class with class attributes rather than an Enum
    because NVFLARE events are string constants.
    """

    # Common events (all job types)
    TRAINING_INITIATED = "_training_initiated"
    RESULTS_UPLOAD_STARTED = "_results_upload_started"
    RESULTS_UPLOAD_COMPLETED = "_results_upload_completed"
    SEND_RESULT = "_send_result"
    LOG_EXCEPTION = "_log_exception"
    ABORTED = "_aborted"

    # Evaluation-specific events
    TASK_INITIATED = "_task_initiated"


class ModelStatus(str, Enum):
    """Model training status values."""

    PENDING = "PENDING"
    INITIATED = "INITIATED"
    PREPARED = "PREPARED"
    TRAINING_STARTED = "TRAINING_STARTED"
    RESULTS_UPLOADED = "RESULTS_UPLOADED"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class FlipMetricsLabel(str, Enum):
    """Standard metric labels for FLIP metrics reporting."""

    LOSS_FUNCTION = "LOSS_FUNCTION"
    DL_RESULT = "DL_RESULT"
    AVERAGE_SCORE = "AVERAGE_SCORE"


class FlipMetaKey(str, Enum):
    """Metadata keys used in FLIP (diffusion model specific)."""

    STAGE = "stage"
