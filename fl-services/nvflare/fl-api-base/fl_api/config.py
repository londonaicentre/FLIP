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

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict()

    USERNAME: str = "admin@nvidia.com"
    SECURE_MODE: bool = True

    LOG_LEVEL: str = "INFO"

    FL_ADMIN_DIRECTORY: str

    TIMEOUT_SESSION_CONNECT: float = 20.0

    # The GPU request, per site, for a job that declares none (FLIP#70). A job declares its own in its
    # config.json RESOURCE_SPEC, and a researcher can override that when submitting training; see
    # fl_api/utils/job_resources.py. NVFLARE's scheduler holds a job until every site can provide the
    # request, so a non-zero default here keeps every job off a CPU-only site, which is what stalled the
    # Azure trust under stag's default of 1 (FLIP#1390).
    JOB_RESOURCE_SPEC_NUM_GPUS: int = Field(default=0, ge=0)
    JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB: int = Field(default=0, ge=0)

    # Job configuration defaults, used when the user-provided config is missing these values.
    JOB_CONFIG_DEFAULT_LOCAL_ROUNDS: int = 1
    JOB_CONFIG_DEFAULT_GLOBAL_ROUNDS: int = 1


# Eager load once (for app use)
_settings = Settings()  # type: ignore


# Accessor to allow override in tests
def get_settings() -> Settings:
    """
    Get the application settings.

    Returns:
        Settings: An instance of the Settings class containing configuration values.
    """
    return _settings  # type: ignore
