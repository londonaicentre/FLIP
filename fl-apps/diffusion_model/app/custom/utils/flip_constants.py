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

from enum import Enum

from pydantic import HttpUrl, PositiveInt, field_validator
from pydantic_settings import BaseSettings


class _Common(BaseSettings):
    """Fields shared by both environments."""

    LOCAL_DEV: bool  # explicit in both
    MIN_CLIENTS: PositiveInt = 1


class DevSettings(_Common):
    """Dev config: only dev fields exist; prod fields don't even exist here."""

    LOCAL_DEV: bool = True

    DEV_DATAFRAME: str
    DEV_IMAGES_DIR: str


class ProdSettings(_Common):
    """Prod config: only prod fields exist; dev fields don't exist here."""

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


# instantiate once and import elsewhere
FlipConstants = DevSettings() if _Common().LOCAL_DEV else ProdSettings()


class ResourceType(str, Enum):
    DICOM = "DICOM"
    NIFTI = "NIFTI"
    SEGMENTATION = "SEG"
    ALL = "ALL"


class FlipTasks(str, Enum):
    INIT_TRAINING = "init_training"
    POST_VALIDATION = "post_validation"
    CLEANUP = "cleanup"


class FlipEvents(object):
    TRAINING_INITIATED = "_training_initiated"
    RESULTS_UPLOAD_STARTED = "_results_upload_started"
    RESULTS_UPLOAD_COMPLETED = "_results_upload_completed"
    SEND_RESULT = "_send_result"
    LOG_EXCEPTION = "_log_exception"
    ABORTED = "_aborted"


class ModelStatus(str, Enum):
    PENDING = "PENDING"
    INITIATED = "INITIATED"
    PREPARED = "PREPARED"
    TRAINING_STARTED = "TRAINING_STARTED"
    RESULTS_UPLOADED = "RESULTS_UPLOADED"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class FlipMetricsLabel(str, Enum):
    LOSS_FUNCTION = "LOSS_FUNCTION"
    DL_RESULT = "DL_RESULT"
    AVERAGE_SCORE = "AVERAGE_SCORE"


class FlipMetaKey(str, Enum):
    STAGE = "stage"
