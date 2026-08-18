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

from typing import Literal, Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings
from sqlalchemy.engine import make_url

# The kit-template placeholder for the not-yet-minted XNAT credentials
# (trust/.env.example, FLIP-PT-056) — never a real password.
_XNAT_CREDENTIAL_PLACEHOLDER = "<run-make-generate-xnat-credentials>"


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
    # XNAT's own AE title. Must match XNAT_AETITLE in configure-xnat.sh, because this value becomes
    # the C-MOVE destination handed to the PACS and DQR matches it against a registered SCP receiver
    # by exact AE title and port (FLIP#993).
    XNAT_AETITLE: str = "XNAT"
    # AE title of the upstream PACS. The PACS id is resolved from this at runtime, so a deployment
    # whose PACS is not id 1 — an extra registration, or a re-registered entry — still works.
    PACS_AETITLE: str = "ORTHANC"
    # Fallback used only when the AE-title lookup cannot reach XNAT. Historically XNAT registered
    # exactly one PACS and assigned it id 1.
    PACS_ID: int = 1

    # Internal trust-network URLs: docker service name + the service's container
    # port. Fixed by the compose topology, so they default here rather than being
    # required kit fields (override via env only for a non-standard deployment).
    # The XNAT service-account credentials stay required.
    XNAT_URL: str = "http://xnat-web:8080"
    XNAT_SERVICE_USER: str
    XNAT_SERVICE_PASSWORD: str
    # Passwordless by design (FLIP-PT-056): topology only, so no credential is
    # baked into the image. The K8s chart ships the same form
    # (values.yaml imagingApi.env.XNAT_DATABASE_URL).
    XNAT_DATABASE_URL: str = "postgresql+asyncpg://xnat@xnat-db:5432/xnat"
    # The minted per-trust XNAT DB password (kit-file XNAT_DATASOURCE_PASSWORD,
    # FLIP-PT-056). When set, it replaces the password embedded in
    # XNAT_DATABASE_URL, so the URL itself stays a non-secret topology constant.
    # Empty or still the kit-template placeholder → the URL is used as-is, i.e.
    # passwordless, and a pre-mint deployment fails on its first query rather
    # than silently authenticating with a known-weak credential.
    XNAT_DATASOURCE_PASSWORD: str = ""

    @model_validator(mode="after")
    def apply_xnat_datasource_password(self) -> Self:
        """Splice the minted XNAT DB password into ``XNAT_DATABASE_URL``.

        Returns:
            Self: The settings with ``XNAT_DATABASE_URL`` carrying the minted
            password when one is configured.
        """
        if self.XNAT_DATASOURCE_PASSWORD and self.XNAT_DATASOURCE_PASSWORD != _XNAT_CREDENTIAL_PLACEHOLDER:
            url = make_url(self.XNAT_DATABASE_URL).set(password=self.XNAT_DATASOURCE_PASSWORD)
            self.XNAT_DATABASE_URL = url.render_as_string(hide_password=False)
        return self

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
