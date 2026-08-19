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

import json
import logging
from typing import Annotated, Literal

from pydantic import EmailStr, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    # Raised to 5 GiB so large evaluation checkpoints (e.g. the ~759 MiB Ark+
    # weights, ~1.5 GiB for the multimodel variant) can be uploaded; the FL API
    # stages such checkpoints server-side rather than bundling them into the app.
    MAX_MODEL_FILE_BYTES: int = 5 * 1024 * 1024 * 1024
    # Default equals the MAX_PRESIGNED_URL_TTL_SECONDS security ceiling in
    # utils/s3_client.py: anything higher would be silently clamped anyway,
    # with a per-call warning — so a default-configured deployment would log
    # that warning on every presigned upload/download. Operators may set a
    # lower value; higher values are clamped (with the warning as audit trail).
    PRE_SIGNED_URL_EXPIRATION_SECONDS: int = 1800

    # Extension whitelist for model-file uploads, enforced before minting the
    # presigned POST policy so disallowed types never reach S3. Suffixes are
    # matched case-insensitively against the end of the filename. Env format:
    # JSON list or comma-separated string (NoDecode defers parsing to the
    # validator below). Opaque archives (.zip/.tar) are deliberately excluded —
    # their contents can't be gated by the scan pipeline.
    ALLOWED_MODEL_FILE_EXTENSIONS: Annotated[list[str], NoDecode] = [
        ".py",
        ".json",
        # Flower's per-app run config. `config.toml` sits beside the app code in
        # every Flower template and tutorial; fl-api-flower feeds it to
        # `flwr run --run-config` to override the bundle pyproject's
        # [tool.flwr.app.config]. Omitting it would reject every Flower upload.
        ".toml",
        ".pt",
        ".pth",
        ".pkl",
        ".txt",
        ".yaml",
        ".yml",
        ".safetensors",
    ]
    # Suffixes given a structural picklescan before promotion to the scanned
    # bucket: pickle-bearing formats where deserialisation is code execution
    # by design. Subset of ALLOWED_MODEL_FILE_EXTENSIONS in practice, kept
    # separate so widening the upload whitelist never silently skips scanning.
    PICKLESCAN_FILE_SUFFIXES: Annotated[list[str], NoDecode] = [".pt", ".pth", ".pkl", ".pickle"]
    # Hard wall-clock cap on a single picklescan run. A timeout is treated as
    # a scan failure (fail-closed -> ERROR), never as clean.
    PICKLESCAN_TIMEOUT_SECONDS: int = 120

    # Hard wall-clock cap on a single Bandit run over a .py upload (#877).
    # Unlike PICKLESCAN_TIMEOUT_SECONDS, a timeout here just means no findings
    # are recorded (fail-open) — Bandit is an advisory signal, not a gate.
    BANDIT_TIMEOUT_SECONDS: int = 60

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
    SCHEDULER_MALWARE_SCAN_RECONCILE_RATE: int = 1  # How often to reconcile stuck SCANNING uploads (in minutes)
    # How often to ask each net's FL API whether an in-flight job has failed (in minutes).
    # Bounds how long a run that dies after submission can leave its model looking alive.
    SCHEDULER_FL_JOB_RECONCILE_RATE: int = 1
    # How long (in minutes) an in-flight job may go unlisted by its FL backend before the
    # reconcile treats it as dead. Covers a backend restart losing its run state (the
    # SuperLink's is in-memory by default): the run can never report, so waiting longer
    # just leaves the model looking alive. Generous so a transient listing hiccup never
    # errors a healthy run.
    FL_JOB_UNLISTED_GRACE_MINUTES: int = 30

    # Database settings
    DB_PORT: int
    DB_HOST: str = "localhost"
    POSTGRES_USER: str
    POSTGRES_DB: str

    # Variables used during database seeding
    NET_ENDPOINTS: dict[str, str]

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
        """Treat empty-string MAX_MODEL_FILE_BYTES as the field default 5 GiB.

        GitHub Actions environments inject empty-string env vars for every
        var that isn't explicitly set in the environment scope; Pydantic
        treats that as a real override and rejects it against ``int``.
        Same shape as ``coerce_empty_env`` / ``coerce_empty_mfa``. Must stay
        in sync with the ``MAX_MODEL_FILE_BYTES`` field default above.
        """
        if v is None or v == "":
            return 5 * 1024 * 1024 * 1024
        return v

    @field_validator("PRE_SIGNED_URL_EXPIRATION_SECONDS", mode="before")
    @classmethod
    def coerce_empty_pre_signed_url_expiration(cls, v: object) -> object:
        """Treat empty-string PRE_SIGNED_URL_EXPIRATION_SECONDS as the default 1800s.

        Same rationale as ``coerce_empty_max_model_file_bytes``. Must stay in
        sync with the field default above (== the presigned-URL TTL ceiling).
        """
        if v is None or v == "":
            return 1800
        return v

    @field_validator(
        "PICKLESCAN_TIMEOUT_SECONDS",
        "BANDIT_TIMEOUT_SECONDS",
        "SCHEDULER_MALWARE_SCAN_RECONCILE_RATE",
        mode="before",
    )
    @classmethod
    def coerce_empty_scan_int(cls, v: object, info: ValidationInfo) -> object:
        """Treat an empty-string scan-timing setting as the field default.

        Same rationale as ``coerce_empty_max_model_file_bytes``: these arrive
        as empty strings whenever the name appears in an env file at all —
        including *commented out*, since the Makefile's
        ``export $(shell sed 's/=.*//' ../.env.development)`` strips the value
        from every line and exports the bare name. Pydantic treats that as a
        real override and rejects it against ``int``.
        """
        if v is None or v == "":
            return cls.model_fields[info.field_name].default  # type: ignore[index]
        return v

    @field_validator("ALLOWED_MODEL_FILE_EXTENSIONS", "PICKLESCAN_FILE_SUFFIXES", mode="before")
    @classmethod
    def parse_suffix_list(cls, v: object, info: ValidationInfo) -> object:
        """Parse the suffix-list env vars and normalise every entry.

        The fields carry ``NoDecode``, so env input arrives here as the raw
        string: a JSON list (``[".py", ".pt"]``) or a comma-separated string
        (``.py,.pt``) are both accepted, and empty/unset falls back to the
        field default. Entries are lowercased and given a leading dot so
        matching stays purely suffix-based regardless of how the operator
        wrote them.

        A value that contains only separators or whitespace (``",,,"``,
        ``" , "``) normalises to nothing, and is treated exactly like the
        empty case — it falls back to the default rather than yielding an
        empty list. An empty list would fail *open* in the worst way for
        ``PICKLESCAN_FILE_SUFFIXES``: no suffix would ever match, so every
        upload would skip scanning silently. Never let a malformed value
        disable the gate.
        """
        if v is None or v == "" or v == []:
            v = cls.model_fields[info.field_name].default  # type: ignore[index]
        if isinstance(v, str):
            stripped = v.strip()
            v = json.loads(stripped) if stripped.startswith("[") else stripped.split(",")
        if isinstance(v, list):
            normalised = [
                entry if entry.startswith(".") else f".{entry}"
                for entry in (str(raw).strip().lower() for raw in v)
                if entry
            ]
            if not normalised:
                default = cls.model_fields[info.field_name].default  # type: ignore[index]
                # flip_api.utils.logger imports get_settings(), so it cannot be
                # imported here without a cycle — use the same underlying logger.
                logging.getLogger("uvicorn").warning(
                    f"{info.field_name} resolved to an empty list from {v!r}; "
                    f"falling back to the default {default}"
                )
                return default
            return normalised
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

    # FL kit slot pool names — one per pre-provisioned FL kit (workspace/net-N/services/<slot>).
    # Seeded into `fl_kit_slot` so POST /admin/trusts can hand each joining trust the next
    # free slot regardless of the trust's friendly name. Dev-only on purpose: in production
    # the pool's single source is the /flip/fl_kit_slot_names SSM parameter (see
    # db/seed/fl_kit_slots.resolve_fl_kit_slot_names) — there is no env-var fallback, so a
    # broken parameter can't be silently masked by stale task-definition env. Defaults to []
    # so existing dev envs aren't required to set it; in that case the pool is empty until
    # the admin provisions kits and adds them here.
    FL_KIT_SLOT_NAMES: list[str] = []


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
