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

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # Internal trust-network URLs (docker service name + container port), fixed by
    # the compose topology — default here instead of being required kit fields.
    DATA_ACCESS_API_URL: str = "http://data-access-api:8000"
    IMAGING_API_URL: str = "http://imaging-api:8000"
    TRUST_API_KEY: str
    TRUST_API_KEY_HEADER: str
    AES_KEY_BASE64: str  # Shared key for decrypting task payloads from the hub

    # Trust-internal service auth — sent on every call to imaging-api so it can
    # distinguish authorised callers (this trust-api, fl-client) from anything
    # else on the trust Docker network. Per-trust secret; never sent to the hub.
    TRUST_INTERNAL_SERVICE_KEY: str = ""
    TRUST_INTERNAL_SERVICE_KEY_HEADER: str = "X-Trust-Internal-Service-Key"

    # Polling configuration
    POLL_INTERVAL_SECONDS: int = 5  # How often to poll the hub for tasks (seconds)

    # Optional misconfig self-check. On first contact the hub returns the
    # ``{trust_id, trust_name}`` it resolved this host as. When ``EXPECTED_TRUST_ID``
    # is set (in the kit file) and the hub reports a different id, the trust-api
    # exits non-zero instead of silently authenticating as the wrong trust — the
    # typical cause is the operator deploying the wrong kit to the wrong host.
    # Leaving it empty disables the check; the loud "Authenticated to hub as …"
    # log line is emitted regardless.
    EXPECTED_TRUST_ID: str = ""

    # Timeout for cohort query requests to data-access-api (seconds)
    COHORT_QUERY_TIMEOUT_SECONDS: int = 300

    # Health collector (per-container status attached to the heartbeat, surfaced on
    # the hub's Connection Status page). URLs/ports are fixed by the compose topology —
    # default here instead of being required kit fields.
    HEALTH_COLLECT_INTERVAL_SECONDS: int = 30  # How often to probe the trust services (seconds)
    HEALTH_PROBE_DEGRADED_MS: int = 1000  # Successful probe slower than this reports "degraded"
    XNAT_URL: str = "http://xnat-web:8080"
    PACS_ID: int = 1  # XNAT DQR PACS id used for the ping_pacs deep probe (matches imaging-api's default)
    OMOP_DB_HOST: str = "omop-db"
    OMOP_DB_PORT: int = 5432


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
