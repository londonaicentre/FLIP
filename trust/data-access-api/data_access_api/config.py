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

from pydantic import PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Common settings shared across all environments (development and production)."""

    # Environment flag
    ENV: Literal["development", "production"] = "development"

    # env file is 3 directories up from this file
    # Get current directory: data-access-api/data_access_api/config.py
    model_config = SettingsConfigDict(
        env_file=[
            str(Path(__file__).parent.parent.parent.parent / f".env.{ENV}"),
        ],
        env_file_encoding="utf-8",
        extra="allow",
    )

    #
    LOG_LEVEL: str = "INFO"

    #
    COHORT_QUERY_THRESHOLD: int = 10  # Minimum number of records required to return statistics
    CACHE_TTL_DAYS: int = 60  # Number of days before cached query results expire
    CACHE_MAX_RESULT_ROWS: PositiveInt = 50_000  # Max rows per cached result; larger results skip caching
    CACHE_MAX_ENTRIES: PositiveInt = 64  # Max number of cached query results

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
        """Construct the database URL for the OMOP database.

        Returns:
            SecretStr: A ``postgresql://`` URL wrapped as a SecretStr to avoid leaking the password
            in logs or error messages.
        """
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
