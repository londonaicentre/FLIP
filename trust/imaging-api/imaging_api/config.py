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
    # XNAT's own AE title. Must match XNAT_AETITLE in configure-xnat.sh, because this value becomes
    # the C-MOVE destination handed to the PACS and DQR matches it against a registered SCP receiver
    # by exact AE title and port (FLIP#993).
    XNAT_AETITLE: str = "XNAT"
    # No PACS id setting. Queries name no PACS, so DQR uses its default query/retrieve PACS, and each
    # import reads that PACS's id from XNAT (services/imaging.py) — the id is XNAT's to assign, so a
    # configured one could only be a guess.

    # Internal trust-network URLs: docker service name + the service's container
    # port. Fixed by the compose topology, so they default here rather than being
    # required kit fields (override via env only for a non-standard deployment).
    # The XNAT service-account credentials stay required.
    XNAT_URL: str = "http://xnat-web:8080"
    XNAT_SERVICE_USER: str
    XNAT_SERVICE_PASSWORD: str

    #
    DATA_ACCESS_API_URL: str = "http://data-access-api:8000"

    # Container Service image for automatic DICOM→NIfTI conversion. The per-project
    # event subscription looks the XNAT command up by this exact image string, so it
    # must match what trust/xnat/xnat/config/dcm2niix_command.json registers at deploy
    # time (configure-dcm2niix.sh, on Compose and Kubernetes). Pinned by version tag, never
    # `latest`: Docker Hub's mutable `xnat/dcm2niix:latest` resolved to a 2021 build
    # that silently dropped slices from valid series (FLIP#980).
    DCM2NIIX_IMAGE: str = "ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724"

    # Which machinery converts DICOM to NIfTI (FLIP#1583).
    #
    # `container-service` is everything that existed before and stays the default, so
    # Compose and Kubernetes keep launching dcm2niix through XNAT's Container Service with
    # no change at all. `snowflake` is the only option that works on Snowpark Container
    # Services, which has no Docker socket, no swarm and no Kubernetes backend for the
    # plugin to drive: imaging-api instead CALLs the owner's-rights procedure
    # APP.CONVERT_SCAN, polls APP.CONVERT_STATUS, reads the output from its own mount of
    # the CONVERTER_IO stage and registers the files in XNAT over REST.
    #
    # The gate is a setting rather than an auto-detection on purpose: "am I on SPCS?" has
    # no reliable answer, and a wrong guess silently picks a converter that cannot run.
    CONVERTER_BACKEND: Literal["container-service", "snowflake"] = "container-service"

    # The rest of this block is read only when CONVERTER_BACKEND == "snowflake"; the SPCS
    # specification sets every one of them (trust/deploy/snowflake/spec_templates.py).
    #
    # No password and no private key: the OAuth session token SPCS mounts IS the
    # credential, and the connector is given authenticator="oauth". SNOWFLAKE_HOST and
    # SNOWFLAKE_ACCOUNT are injected into every SPCS container by the platform, which is
    # why they default to empty here rather than being required.
    SNOWFLAKE_HOST: str = ""
    SNOWFLAKE_ACCOUNT: str = ""
    SNOWFLAKE_DATABASE: str = ""
    SNOWFLAKE_SCHEMA: str = "APP"
    SNOWFLAKE_TOKEN_FILE: str = "/snowflake/session/token"
    # Where the converter job's output appears, from this container's point of view: the
    # mount of the CONVERTER_IO stage the procedure writes under.
    CONVERTER_IO_DIR: str = "/converter-io"
    # Seconds between CONVERT_STATUS polls, and the ceiling on one scan's conversion.
    CONVERT_POLL_SECONDS: int = 15
    CONVERT_TIMEOUT_SECONDS: int = 1800

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
