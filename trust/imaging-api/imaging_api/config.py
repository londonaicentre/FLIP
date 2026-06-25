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

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Common settings shared across all environments (development and production)."""

    # Environment flag
    ENV: Literal["development", "production"] = "development"

    @field_validator("ENV", mode="before")
    @classmethod
    def coerce_empty_env(cls, v: str) -> str:
        """Treat empty-string ENV (e.g. from CI environment injection) as 'development'."""
        if v is None or v == "":
            return "development"
        return v

    #
    LOG_LEVEL: str = "INFO"

    #
    XNAT_PORT: int
    # XNAT registers exactly one PACS (configure-xnat.sh), which it assigns id 1,
    # so this defaults to 1 rather than being a required, mis-settable kit field.
    PACS_ID: int = 1

    # Internal trust-network URLs: docker service name + the service's container
    # port. Fixed by the compose topology, so they default here rather than being
    # required kit fields (override via env only for a non-standard deployment).
    # The XNAT service-account credentials stay required.
    XNAT_URL: str = "http://xnat-web:8080"
    XNAT_SERVICE_USER: str
    XNAT_SERVICE_PASSWORD: str
    XNAT_DATABASE_URL: str = "postgresql+asyncpg://xnat:xnat@xnat-db:5432/xnat"

    #
    DATA_ACCESS_API_URL: str = "http://data-access-api:8000"

    #
    BASE_IMAGES_DOWNLOAD_DIR: str

    #
    AES_KEY_BASE64: str

    # Trust-internal service auth — protects every router except /health from
    # unauthenticated callers on the trust Docker network or via SSM port-forward,
    # and is also forwarded outbound on calls to data-access-api. Every trust-
    # internal service holds the same per-trust key in plaintext; the receiver
    # validates with a constant-time compare.
    TRUST_INTERNAL_SERVICE_KEY_HEADER: str = "X-Trust-Internal-Service-Key"
    TRUST_INTERNAL_SERVICE_KEY: str = ""

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
