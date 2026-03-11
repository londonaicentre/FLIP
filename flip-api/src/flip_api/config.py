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

from typing import Literal, Optional

from pydantic import EmailStr, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Common settings shared across all environments (development and production)."""

    model_config = SettingsConfigDict(
        env_file="../.env.development",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment flag
    ENV: Literal["development", "production"] = "development"

    PRIVATE_API_KEY_HEADER: str
    PRIVATE_API_KEY: str

    # AWS settings
    AWS_PROFILE: Optional[str] = None
    AWS_REGION: str
    AWS_COGNITO_USER_POOL_ID: str
    AWS_COGNITO_APP_CLIENT_ID: str
    AWS_SECRET_NAME: str
    AWS_SES_ADMIN_EMAIL_ADDRESS: EmailStr  # e.g. admin@example.com
    AWS_SES_SENDER_EMAIL_ADDRESS: EmailStr  # e.g. no-reply@example.com, but can be same as admin email

    # S3 bucket settings
    UPLOADED_MODEL_FILES_BUCKET: str
    SCANNED_MODEL_FILES_BUCKET: str
    UPLOADED_FEDERATED_DATA_BUCKET: str
    FL_APP_BASE_BUCKET: str
    FL_APP_DESTINATION_BUCKET: str
    PRE_SIGNED_URL: Optional[str] = None

    # Reimport imaging project studies
    PROJECT_REIMPORT_RATE: int = 60  # How often to reimport studies for a given project (in minutes)
    MAX_REIMPORT_COUNT: int = 5

    # Scheduler settings
    SCHEDULE_RUN_JOBS_EXECUTION: bool = True
    SCHEDULER_RUN_JOBS_RATE: int = 1  # in minutes
    SCHEDULER_KEEP_FL_API_SESSION_ALIVE_RATE: int = 2  # in minutes
    SCHEDULER_REIMPORT_IMAGING_PROJECT_STUDIES_RATE: int = (
        30  # How often to check for projects with unimported studies (in minutes)
    )

    # Database settings
    DB_PORT: int
    DB_HOST: str = "localhost"
    POSTGRES_USER: str
    POSTGRES_DB: str

    # Variables used during database seeding
    NET_ENDPOINTS: dict[str, str]

    # SSL / TLS settings
    TRUST_CA_BUNDLE: Optional[str] = None  # Path to the Trust CA certificate PEM file

    # FL settings
    FL_BACKEND: Literal["nvflare", "flower"] = "nvflare"

    # Variables only used in testing
    FLIP_API_URL: str = "http://localhost:8080/"  # this is currently only used in tests (TODO review)
    ADMIN_USER_PASSWORD: SecretStr | None = None  # only used in integration tests to make actual logins


class DevSettings(Settings):
    """Settings specific to local or development environment."""

    ENV: Literal["development"] = "development"
    POSTGRES_PASSWORD: str  # in dev, get DB password from env variable

    AES_KEY_BASE64: str  # in dev, get AES key from env variable

    TRUST_ENDPOINTS: dict[str, str]  # in dev, get trust endpoints from env variables


class ProdSettings(Settings):
    """Settings specific to production environment."""

    ENV: Literal["production"] = "production"
    POSTGRES_SECRET_ARN: str  # in prod, get DB password from secrets manager, using this ARN


# Eager load once (for app use)
_settings = DevSettings() if Settings().ENV == "development" else ProdSettings()  # type: ignore


# Accessor to allow override in tests
def get_settings() -> DevSettings | ProdSettings:
    """
    Get the application settings.

    Returns:
        DevSettings | ProdSettings: An instance of Settings containing configuration values.
    """
    return _settings
