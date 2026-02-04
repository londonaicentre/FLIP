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

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROD: bool = bool(int(os.getenv("PROD", "0")))
    environment: str = "development" if not PROD else "production"
    LOG_LEVEL: str = "DEBUG"

    #
    XNAT_PORT: int
    PACS_ID: int

    #
    XNAT_URL: str
    XNAT_SERVICE_USER: str
    XNAT_SERVICE_PASSWORD: str
    XNAT_DATABASE_URL: str

    #
    DATA_ACCESS_API_URL: str

    #
    BASE_IMAGES_DOWNLOAD_DIR: str

    #
    AES_KEY_BASE64: str

    # Reimport settings
    REIMPORT_STUDIES_ENABLED: bool = True


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
