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

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env file is 3 directories up from this file
    # Get current directory: imaging-api/imaging_api/config.py
    ENV: str = os.getenv("ENV", "development")
    environment: str = ENV

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent.parent.parent / f".env.{environment}"),
        env_file_encoding="utf-8",
        extra="allow",
    )

    #
    CENTRAL_HUB_API_URL: str
    DATA_ACCESS_API_URL: str
    IMAGING_API_URL: str
    TRUST_API_KEY: str
    TRUST_API_KEY_HEADER: str
    AES_KEY_BASE64: str  # Shared key for decrypting task payloads from the hub

    # Polling configuration
    TRUST_NAME: str  # Must match Trust.name in hub DB (e.g. "Trust_1")
    POLL_INTERVAL_SECONDS: int = 5  # How often to poll the hub for tasks (seconds)

    # Timeout for cohort query requests to data-access-api (seconds)
    COHORT_QUERY_TIMEOUT_SECONDS: int = 300


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
