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

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env file is 3 directories up from this file
    # Get current directory: data-access-api/data_access_api/config.py
    PROD: bool = bool(int(os.getenv("PROD", "0")))
    environment: str = "development" if not PROD else "production"
    model_config = SettingsConfigDict(
        env_file=[
            str(Path(__file__).parent.parent.parent.parent / f".env.{environment}"),
        ],
        env_file_encoding="utf-8",
        extra="allow",
    )

    #
    COHORT_QUERY_THRESHOLD: int = 10  # Minimum number of records required to return statistics

    #
    OMOP_DB_SERVICE_NAME: str = "omop-db"  # The name of the OMOP database service in Docker Compose or Kubernetes
    OMOP_DB_PORT: int = 5432  # Calls from another container use port 5432 (e.g. http://omop-db:5432)
    DATA_ACCESS_POSTGRES_USER: str
    DATA_ACCESS_POSTGRES_PASSWORD: SecretStr
    OMOP_POSTGRES_DB: str

    #
    AES_KEY_BASE64: str

    # Define the database URL for the OMOP database
    @property
    def OMOP_DATABASE_URL(self) -> SecretStr:
        """Construct the database URL for the OMOP database."""
        return SecretStr(
            f"postgresql://{self.DATA_ACCESS_POSTGRES_USER}:{self.DATA_ACCESS_POSTGRES_PASSWORD.get_secret_value()}@{self.OMOP_DB_SERVICE_NAME}:{self.OMOP_DB_PORT}/{self.OMOP_POSTGRES_DB}"
        )


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
