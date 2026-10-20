#!/usr/bin/env python3
#
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

"""The Snowflake object-naming contract for a FLIP trust (FLIP#1589).

One module so that the two halves of the Snowflake node shape cannot drift: the spec
renderer (``render_specs.py``) and the kit sync (``sync_snowflake_kit.py``) derive every
object name from here, and the Terraform root (FLIP#1581) creates the same names from
the same written contract. A name that exists in only one of the three is a deploy that
fails at ``CREATE SERVICE`` with an object that does not exist, which is the failure this
module exists to make impossible on our side of the line.

``CODE`` is the trust code from the kit (``KIT=<CODE>``), upper-cased. One trust per
database; several trusts may share an account, which is why every account-level object
carries the ``FLIP_<CODE>_`` prefix while database-level objects are merely qualified.
"""

from __future__ import annotations

import re

#: Schemas of the trust database. APP holds services, secrets, network rules and the
#: image repository; OMOP the clinical tables (FLIP#1580); STAGES the internal stages;
#: OBS the event-table views (FLIP#1584).
SCHEMAS = ("APP", "OMOP", "STAGES", "OBS")

#: Internal stages, all ENCRYPTION=(TYPE='SNOWFLAKE_SSE').
STAGES = (
    "FL_KIT",
    "SEED",
    "XNAT_ARCHIVE",
    "IMAGES",
    "CONVERTER_IO",
    "GOVERNANCE",
    "COHORT_SNAPSHOTS",
)

#: Secrets (APP schema, GENERIC_STRING). Values are written ONLY by `make sync-kit`,
#: never by Terraform, and never appear in a rendered specification.
SECRETS = (
    "TRUST_API_KEY",  # pragma: allowlist secret
    "TRUST_INTERNAL_SERVICE_KEY",  # pragma: allowlist secret
    "AES_KEY_BASE64",  # pragma: allowlist secret
    "XNAT_ADMIN_PASSWORD",  # pragma: allowlist secret
    "XNAT_SERVICE_PASSWORD",  # pragma: allowlist secret
    "XNAT_DB_PASSWORD",  # pragma: allowlist secret
    "ORTHANC_PASSWORD",  # pragma: allowlist secret
)

#: Kit variable each secret object takes its value from. The kit is the only source;
#: a secret with no kit variable (none today) would have to be minted elsewhere.
SECRET_KIT_VAR = {
    "TRUST_API_KEY": "TRUST_API_KEY",  # pragma: allowlist secret
    "TRUST_INTERNAL_SERVICE_KEY": "TRUST_INTERNAL_SERVICE_KEY",  # pragma: allowlist secret
    "AES_KEY_BASE64": "AES_KEY_BASE64",  # pragma: allowlist secret
    "XNAT_ADMIN_PASSWORD": "XNAT_ADMIN_PASSWORD",  # pragma: allowlist secret
    "XNAT_SERVICE_PASSWORD": "XNAT_SERVICE_PASSWORD",  # pragma: allowlist secret
    "XNAT_DB_PASSWORD": "XNAT_DATASOURCE_PASSWORD",  # pragma: allowlist secret
    "ORTHANC_PASSWORD": "ORTHANC_PASSWORD",  # pragma: allowlist secret
}

CODE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,32}$")


class NamingError(ValueError):
    """The trust CODE cannot be turned into Snowflake object names."""


def normalise_code(code: str) -> str:
    """Upper-case and validate a trust CODE.

    Every name below is built by interpolation, so a CODE carrying a quote, a dot or a
    space would produce either an unusable identifier or (in the SQL the kit sync emits)
    an injection point. Refusing here is the one place that cannot be forgotten.

    Raises:
        NamingError: The code is empty or is not a bare SQL identifier.
    """
    stripped = (code or "").strip()
    if not CODE_PATTERN.match(stripped):
        raise NamingError(
            f"trust CODE {code!r} is not usable as a Snowflake identifier: it must start with a "
            "letter and contain only letters, digits and underscores"
        )
    return stripped.upper()


class Names:
    """Every Snowflake object name for one trust, derived from its CODE."""

    def __init__(self, code: str) -> None:
        self.code = normalise_code(code)

    # ── account-level ────────────────────────────────────────────────────
    @property
    def admin_role(self) -> str:
        return f"FLIP_{self.code}_ADMIN"

    @property
    def deploy_role(self) -> str:
        return f"FLIP_{self.code}_DEPLOY"

    @property
    def service_role(self) -> str:
        return f"FLIP_{self.code}_SVC"

    @property
    def omop_reader_role(self) -> str:
        return f"FLIP_{self.code}_OMOP_READER"

    @property
    def warehouse(self) -> str:
        return f"FLIP_{self.code}_WH"

    @property
    def pool(self) -> str:
        return f"FLIP_{self.code}_POOL"

    @property
    def job_pool(self) -> str:
        return f"FLIP_{self.code}_JOB_POOL"

    @property
    def gpu_pool(self) -> str:
        return f"FLIP_{self.code}_GPU_POOL"

    # ── database-level ───────────────────────────────────────────────────
    @property
    def database(self) -> str:
        return f"FLIP_TRUST_{self.code}"

    @property
    def app_schema(self) -> str:
        return f"{self.database}.APP"

    @property
    def stages_schema(self) -> str:
        return f"{self.database}.STAGES"

    @property
    def image_repository(self) -> str:
        """The account image repository, as a SQL name."""
        return f"{self.app_schema}.IMAGES"

    @property
    def image_repository_path(self) -> str:
        """The repository path a specification's ``image:`` field is relative to."""
        return f"/{self.database}/APP/IMAGES"

    def stage(self, name: str) -> str:
        """Fully-qualified stage name, e.g. ``FLIP_TRUST_GSTT.STAGES.IMAGES``."""
        if name not in STAGES:
            raise NamingError(f"{name} is not a stage in the naming contract: {', '.join(STAGES)}")
        return f"{self.stages_schema}.{name}"

    def stage_ref(self, name: str, prefix: str = "") -> str:
        """A stage reference for a spec volume, optionally sliced to a prefix.

        The prefix is how the per-net isolation rule survives onto SPCS: the fl-client
        mounts ``@...IMAGES/net-<N>`` rather than the whole images tree, exactly as the
        Compose client bind-mounts only its own net's subdirectory.
        """
        base = f"@{self.stage(name)}"
        return f"{base}/{prefix.strip('/')}" if prefix else base

    def secret(self, name: str) -> str:
        """Fully-qualified secret object name (never a value)."""
        if name not in SECRETS:
            raise NamingError(f"{name} is not a secret in the naming contract: {', '.join(SECRETS)}")
        return f"{self.app_schema}.{name}"

    def service(self, service: str) -> str:
        """Fully-qualified service name, e.g. ``FLIP_TRUST_GSTT.APP.TRUST_API``."""
        return f"{self.app_schema}.{service.upper().replace('-', '_')}"

    def dns(self, service: str) -> str:
        """The in-account DNS name of a service.

        SPCS derives it from the service, schema and database names, lower-cased with
        underscores turned into hyphens. It is NOT the Compose service name, so every
        inter-service URL in a rendered spec has to be derived rather than copied from
        the compose files (``http://imaging-api:8000`` resolves nowhere on SPCS).
        """
        host = service.lower().replace("_", "-")
        schema = "app"
        database = self.database.lower().replace("_", "-")
        return f"{host}.{schema}.{database}.snowflakecomputing.internal"
