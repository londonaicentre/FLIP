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
    PROD: bool = bool(int(os.getenv("PROD", "0")))
    environment: str = "development" if not PROD else "production"

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent.parent.parent / f".env.{environment}"),
        env_file_encoding="utf-8",
        extra="allow",
    )

    #
    CENTRAL_HUB_API_URL: str
    DATA_ACCESS_API_URL: str
    IMAGING_API_URL: str
    PRIVATE_API_KEY: str
    PRIVATE_API_KEY_HEADER: str


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
