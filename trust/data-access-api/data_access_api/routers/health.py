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

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from data_access_api.config import get_settings
from data_access_api.db.database import get_engine
from data_access_api.utils.logger import logger

router = APIRouter(prefix="/health", tags=["Health"])

# Cheapest possible "can I reach OMOP and am I allowed to read it?" test. Not a table read:
# on a billed Snowflake warehouse every probe is metered compute, and the imaging status poll
# already re-runs the cohort query roughly every 10 s while a project page is open.
_LIVENESS_PROBE = text("SELECT 1")

# The service is a uv "virtual" project (never installed as a distribution), so the
# only version source shared by the repo checkout and the container image is the
# pyproject.toml that sits next to the package (/app in the image).
_PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"


@lru_cache(maxsize=1)
def _service_version() -> str | None:
    """Look up the service version from the adjacent pyproject.toml.

    Returns:
        str | None: The ``[project].version`` value, or None when the file is
        missing or unparsable.
    """
    try:
        with _PYPROJECT_PATH.open("rb") as fh:
            return tomllib.load(fh)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return None


@router.get("")
async def health_check() -> dict[str, str | None]:
    """
    Health check endpoint for the Data Access API

    Returns:
        dict[str, str | None]: The status of the service and the build it runs — the
        CI-baked ``FLIP_RELEASE`` image tag (v<X.Y.Z> or sha-<short7>, FLIP#1204), or the
        pyproject version for a local build that carries none.
    """
    return {"status": "ok", "version": os.environ.get("FLIP_RELEASE") or _service_version()}


@router.get("/omop")
async def omop_health() -> dict[str, str]:
    """
    Report whether the configured OMOP backend is actually reachable.

    trust-api probes OMOP with a raw TCP connect to ``OMOP_DB_HOST:OMOP_DB_PORT`` because it
    deliberately carries no database driver. A Snowflake account is not a TCP port on the trust
    network, so that probe reports a Snowflake trust's OMOP as permanently down. This endpoint
    is the truthful answer for any backend: the service that actually holds the driver opens a
    session and says what happened (FLIP#13).

    Deliberately unauthenticated, like ``/health``: it reports reachability and the backend's
    name, never anything about the data or the credentials.

    Returns:
        dict[str, str]: ``{"status": "ok" | "down", "backend": <backend name>}``.
    """
    backend = get_settings().OMOP_BACKEND
    try:
        with get_engine().connect() as connection:
            connection.execute(_LIVENESS_PROBE)
    except SQLAlchemyError as exc:
        # Category only on the wire: the driver's text can name hosts, roles and pool
        # internals, and this endpoint is unauthenticated.
        logger.error(f"OMOP liveness probe failed on the {backend} backend: {exc}")
        return {"status": "down", "backend": backend}
    return {"status": "ok", "backend": backend}
