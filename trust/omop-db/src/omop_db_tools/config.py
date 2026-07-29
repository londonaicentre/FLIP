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
"""Connection settings for the OMOP database being populated."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Environment-driven connection settings.

    The populate scripts run on the host against a published container port, so
    the host defaults to localhost; override OMOP_DB_HOST when running elsewhere.
    """

    OMOP_DB_HOST: str = "localhost"
    OMOP_DB_PORT: int
    OMOP_POSTGRES_USER: str
    OMOP_POSTGRES_PASSWORD: SecretStr
    OMOP_POSTGRES_DB: str

    @property
    def OMOP_DATABASE_URL(self) -> SecretStr:
        return SecretStr(
            f"postgresql://{self.OMOP_POSTGRES_USER}:{self.OMOP_POSTGRES_PASSWORD.get_secret_value()}"
            f"@{self.OMOP_DB_HOST}:{self.OMOP_DB_PORT}/{self.OMOP_POSTGRES_DB}"
        )


@lru_cache
def get_settings() -> Settings:
    """Get the application settings.

    Returns:
        Settings: An instance of the Settings class containing configuration values.
    """
    return Settings()  # type: ignore[call-arg]
