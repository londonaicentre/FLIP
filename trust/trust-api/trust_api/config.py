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

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Common settings shared across all environments (development and production)."""

    # Environment flag
    ENV: Literal["development", "production"] = "development"

    # env file is 3 directories up from this file
    # Get current directory: imaging-api/imaging_api/config.py
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent.parent.parent / f".env.{ENV}"),
        env_file_encoding="utf-8",
        extra="allow",
    )

    #
    LOG_LEVEL: str = "INFO"

    #
    CENTRAL_HUB_API_URL: str
    DATA_ACCESS_API_URL: str
    IMAGING_API_URL: str
    TRUST_API_KEY: str
    TRUST_API_KEY_HEADER: str
    AES_KEY_BASE64: str  # Shared key for decrypting task payloads from the hub

    # Trust-internal service auth — sent on every call to imaging-api so it can
    # distinguish authorised callers (this trust-api, fl-client) from anything
    # else on the trust Docker network. Per-trust secret; never sent to the hub.
    TRUST_INTERNAL_SERVICE_KEY: str = ""
    TRUST_INTERNAL_SERVICE_KEY_HEADER: str = "X-Trust-Internal-Service-Key"

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
