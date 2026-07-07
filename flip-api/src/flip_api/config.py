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

from pydantic import EmailStr, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from flip_api.domain.schemas.types import FLBackend


class Settings(BaseSettings):
    """Common settings shared across all environments (development and production)."""

    # Environment flag
    ENV: Literal["development", "production"] = "development"

    # Application log level. Defaults to INFO so any unknown environment
    # (prod, stag, any new deploy) emits at INFO and avoids leaking the
    # contents of debug-only diagnostics. Dev compose may override to DEBUG
    # for local development.
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    model_config = SettingsConfigDict(
        env_file=f"../.env.{ENV}",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Per-trust API key header name (for authenticating requests from trusts to the FLIP API)
    TRUST_API_KEY_HEADER: str

    # Internal service auth (fl-server on the Central Hub)
    INTERNAL_SERVICE_KEY_HEADER: str

    # AWS settings
    AWS_PROFILE: str | None = None
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
    FL_APP_DESTINATION_BUCKET: str

    # Local directory holding the base FL application templates (the repo's fl-apps/ tree),
    # baked into the flip-api image and bind-mounted in dev. The bundler walks
    # <FL_APP_BASE_DIR>/<backend>/<job_type>/ and uploads those files into
    # FL_APP_DESTINATION_BUCKET/<model_id>; the per-backend required_files.json manifest is
    # read from <FL_APP_BASE_DIR>/<backend>/required_files.json. Defaults to the baked-in
    # image path; override to mount operator-provided templates. Replaces the former
    # FL_APP_BASE_BUCKET S3 dependency (FLIP#724).
    FL_APP_BASE_DIR: str = "/app/fl-apps"

    # Hard cap on model-file uploads. Bound on the presigned POST policy so
    # S3 rejects oversized payloads at the edge — the hub never sees them.
    MAX_MODEL_FILE_BYTES: int = 100 * 1024 * 1024
    PRE_SIGNED_URL_EXPIRATION_SECONDS: int = 3600

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
    # FL kit slot pool names — one per pre-provisioned FL kit (workspace/net-N/services/<slot>).
    # Seeded into `fl_kit_slot` so POST /admin/trusts can hand each joining trust the next
    # free slot regardless of the trust's friendly name. Defaults to [] so existing dev
    # envs aren't required to set it; in that case the pool is empty until the admin
    # provisions kits and adds them here.
    FL_KIT_SLOT_NAMES: list[str] = []

    # FL backend written onto the FLNets.fl_backend column at seed time (seed_fl_nets). This is
    # canonical: flip-api reads it only at seeding, and the seeded value is never reconciled at
    # runtime. To switch frameworks, `make restart-fl FL_BACKEND=...` recreates flip-api so this
    # seeding re-runs and overwrites the backend on every net. Also used at the deploy layer
    # (compose/Makefile) to pick which fl-* images to run. See fl_scheduler_service.resolve_backend.
    FL_BACKEND: FLBackend = FLBackend.NVFLARE

    # MFA enforcement gate. Defaults to True so every unknown environment
    # (prod, stag, any new deploy) requires TOTP enrolment. Dev compose
    # overrides to False so local developers aren't forced through the
    # enrolment flow on a burner authenticator app just to click around.
    # Not tied to ENV — kept as a dedicated flag so a developer can flip
    # it on locally to verify MFA end-to-end without pretending to be prod.
    ENFORCE_MFA: bool = True

    @field_validator("ENV", mode="before")
    @classmethod
    def coerce_empty_env(cls, v: str) -> str:
        """Treat empty-string ENV (e.g. from CI environment injection) as 'development'."""
        if v is None or v == "":
            return "development"
        return v

    @field_validator("ENFORCE_MFA", mode="before")
    @classmethod
    def coerce_empty_mfa(cls, v: str | bool | None) -> bool:
        """Treat empty-string or None ENFORCE_MFA as the default True."""
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        return v.lower() in ("true", "1")    # type: ignore[union-attr]

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def coerce_log_level(cls, v: object) -> object:
        """Treat empty-string or None LOG_LEVEL as the default INFO; uppercase string input.

        Non-string, non-empty input is returned unchanged so the downstream
        Literal validator rejects it loudly, instead of ``.upper()`` raising
        ``AttributeError`` mid-validation on e.g. ``Settings(LOG_LEVEL=10)``.
        """
        if v is None or v == "":
            return "INFO"
        if isinstance(v, str):
            return v.upper()
        return v

    @field_validator("MAX_MODEL_FILE_BYTES", mode="before")
    @classmethod
    def coerce_empty_max_model_file_bytes(cls, v: object) -> object:
        """Treat empty-string MAX_MODEL_FILE_BYTES as the default 100 MiB.

        GitHub Actions environments inject empty-string env vars for every
        var that isn't explicitly set in the environment scope; Pydantic
        treats that as a real override and rejects it against ``int``.
        Same shape as ``coerce_empty_env`` / ``coerce_empty_mfa``.
        """
        if v is None or v == "":
            return 100 * 1024 * 1024
        return v

    @field_validator("PRE_SIGNED_URL_EXPIRATION_SECONDS", mode="before")
    @classmethod
    def coerce_empty_pre_signed_url_expiration(cls, v: object) -> object:
        """Treat empty-string PRE_SIGNED_URL_EXPIRATION_SECONDS as the default 3600s.

        Same rationale as ``coerce_empty_max_model_file_bytes``.
        """
        if v is None or v == "":
            return 3600
        return v

    # Trust task queue settings
    HEARTBEAT_TIMEOUT_SECONDS: int = 30  # How long since last heartbeat before a trust is considered offline
    TASK_STALE_TIMEOUT_MINUTES: int = 30  # Tasks older than this in IN_PROGRESS are considered stale
    TASK_MAX_RETRIES: int = 3  # Max times a stale task can be retried before being marked FAILED
    SCHEDULER_STALE_TASK_RECOVERY_RATE: int = 10  # How often to check for stale tasks (in minutes)
    MAX_TASK_RESULT_LENGTH: int = 10_000_000  # Max size (in characters) for task result payloads

    # Variables only used in testing
    FLIP_API_URL: str = "http://localhost:8080/api"  # this is currently only used in tests (TODO review)
    ADMIN_USER_PASSWORD: SecretStr | None = None  # only used in integration tests to make actual logins


class DevSettings(Settings):
    """Settings specific to local or development environment."""

    ENV: Literal["development"] = "development"
    POSTGRES_PASSWORD: str  # in dev, get DB password from env variable

    AES_KEY_BASE64: str  # in dev, get AES key from env variable

    INTERNAL_SERVICE_KEY_HASH: str  # in dev, get internal service auth key hash from env variable


class ProdSettings(Settings):
    """Settings specific to production environment.

    Production authenticates to Postgres via RDS Proxy with per-connection IAM
    tokens (see db/database.py), so there is no static DB password setting here.
    """

    ENV: Literal["production"] = "production"


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
