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

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict()

    LOG_LEVEL: str = "INFO"

    FL_ADMIN_DIRECTORY: str

    # GPU resources that the submitted NVFLARE jobs need in order to schedule correctly.
    # TODO Currently this is set globally for all jobs, but we should allow per-job overrides in the future.
    # See https://github.com/londonaicentre/flip/issues/41
    # In development (only 1 GPU available for all clients), changing these settings causes the job to never start
    # ("FL server: "not enough sites have enough resources to start the job""), see
    # https://github.com/londonaicentre/flip/issues/488. We need a setup with >0 GPUs to test this properly.
    JOB_RESOURCE_SPEC_NUM_GPUS: int = 0
    JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB: int = 0


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
