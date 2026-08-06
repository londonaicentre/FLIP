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

"""Background health collector: probes the trust-internal services and caches a snapshot.

The snapshot rides along on the next heartbeat to the hub (see ``task_poller``), which
surfaces it on the Connection Status page. Service keys and statuses form the wire
contract with the hub (``healthy | degraded | down | unknown``):

- ``trust-api`` is never probed — if this loop runs the service is up, so the entry is
  static and the hub derives the row's status from heartbeat age instead.
- ``dicom`` is the PACS connector, probed transitively via imaging-api's ``ping_pacs``
  (imaging-api → XNAT → DIMSE echo). A transport/HTTP failure there proves nothing about
  the PACS itself, so it maps to ``unknown`` rather than ``down``.
- ``omop`` is a raw TCP connect — trust-api deliberately carries no Postgres driver.
"""

import asyncio
import time
import tomllib
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import httpx

from trust_api.config import get_settings
from trust_api.utils.logger import logger

DATA_ACCESS_API_URL = get_settings().DATA_ACCESS_API_URL
IMAGING_API_URL = get_settings().IMAGING_API_URL
XNAT_URL = get_settings().XNAT_URL
PACS_ID = get_settings().PACS_ID
OMOP_DB_HOST = get_settings().OMOP_DB_HOST
OMOP_DB_PORT = get_settings().OMOP_DB_PORT
HEALTH_COLLECT_INTERVAL_SECONDS = get_settings().HEALTH_COLLECT_INTERVAL_SECONDS
HEALTH_PROBE_DEGRADED_MS = get_settings().HEALTH_PROBE_DEGRADED_MS
TRUST_INTERNAL_SERVICE_KEY = get_settings().TRUST_INTERNAL_SERVICE_KEY
TRUST_INTERNAL_SERVICE_KEY_HEADER = get_settings().TRUST_INTERNAL_SERVICE_KEY_HEADER

_PROBE_TIMEOUT_SECONDS = 5.0

# The service is a uv "virtual" project (never installed as a distribution), so the only
# version source shared by the repo checkout and the container image is the pyproject.toml
# that sits next to the package (/app in the image).
_PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"

# Latest snapshot, replaced wholesale each collection cycle; None until the first
# cycle completes so the heartbeat stays bodyless exactly like pre-collector builds.
_snapshot: dict | None = None


def current_snapshot() -> dict | None:
    """Return the most recently collected health snapshot.

    Returns:
        dict | None: The heartbeat-ready body (``{"services": ..., "collected_at": ...}``),
        or None before the first collection cycle completes.
    """
    return _snapshot


@lru_cache(maxsize=1)
def _own_version() -> str | None:
    """Look up this service's version from the adjacent pyproject.toml.

    Returns:
        str | None: The ``[project].version`` value, or None when the file is missing
        or unparsable.
    """
    try:
        with _PYPROJECT_PATH.open("rb") as fh:
            return tomllib.load(fh)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return None


def _entry(status: str, version: str | None = None, response_ms: int | None = None) -> dict:
    """Build one wire-shaped service entry.

    Args:
        status (str): One of ``healthy | degraded | down | unknown``.
        version (str | None): Service version string when known.
        response_ms (int | None): Probe latency in milliseconds when measured.

    Returns:
        dict: ``{"status", "version", "response_ms"}``.
    """
    return {"status": status, "version": version, "response_ms": response_ms}


def _status_from_latency(response_ms: int) -> str:
    """Map a successful probe's latency onto healthy/degraded.

    Args:
        response_ms (int): Measured probe latency in milliseconds.

    Returns:
        str: ``"degraded"`` when slower than ``HEALTH_PROBE_DEGRADED_MS``, else ``"healthy"``.
    """
    return "degraded" if response_ms > HEALTH_PROBE_DEGRADED_MS else "healthy"


def _version_from_body(response: httpx.Response) -> str | None:
    """Extract a string ``version`` field from a JSON response body, tolerating anything.

    Args:
        response (httpx.Response): A successful probe response.

    Returns:
        str | None: The version string, or None when the body is not JSON or carries none.
    """
    try:
        version = response.json().get("version")
    except ValueError:
        return None
    return version if isinstance(version, str) else None


async def _probe_health_endpoint(client: httpx.AsyncClient, url: str) -> dict:
    """Probe a sibling FastAPI service's unauthenticated ``/health`` endpoint.

    Args:
        client (httpx.AsyncClient): HTTP client for making requests.
        url (str): Absolute URL of the ``/health`` endpoint.

    Returns:
        dict: Wire-shaped entry; ``down`` on transport error or non-2xx.
    """
    start = time.monotonic()
    try:
        response = await client.get(url)
    except Exception:
        return _entry("down")
    if not response.is_success:
        return _entry("down")
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return _entry(_status_from_latency(elapsed_ms), _version_from_body(response), elapsed_ms)


async def _probe_xnat(client: httpx.AsyncClient) -> dict:
    """Probe XNAT via its anonymous build-info endpoint (also yields the version).

    ``/xapi/siteConfig/buildInfo`` is served without auth by design (the login page
    renders it). A 401/403 still proves the servlet is alive, so it maps to a
    version-less healthy/degraded rather than ``down`` — defensive against a future
    XNAT locking the endpoint behind auth.

    Args:
        client (httpx.AsyncClient): HTTP client for making requests.

    Returns:
        dict: Wire-shaped entry.
    """
    start = time.monotonic()
    try:
        response = await client.get(f"{XNAT_URL}/xapi/siteConfig/buildInfo")
    except Exception:
        return _entry("down")
    elapsed_ms = int((time.monotonic() - start) * 1000)
    if response.is_success:
        return _entry(_status_from_latency(elapsed_ms), _version_from_body(response), elapsed_ms)
    if response.status_code in (401, 403):
        return _entry(_status_from_latency(elapsed_ms), None, elapsed_ms)
    return _entry("down")


async def _probe_dicom(client: httpx.AsyncClient) -> dict:
    """Probe the PACS connector via imaging-api's ``ping_pacs`` deep endpoint.

    Args:
        client (httpx.AsyncClient): HTTP client for making requests.

    Returns:
        dict: Wire-shaped entry. ``unknown`` when the prober chain (imaging-api → XNAT)
        itself fails, ``down`` only when XNAT reports the DIMSE echo failed.
    """
    headers = {TRUST_INTERNAL_SERVICE_KEY_HEADER: TRUST_INTERNAL_SERVICE_KEY}
    try:
        response = await client.get(f"{IMAGING_API_URL}/imaging/ping_pacs/{PACS_ID}", headers=headers)
    except Exception:
        return _entry("unknown")
    if not response.is_success:
        return _entry("unknown")
    try:
        body = response.json()
    except ValueError:
        return _entry("unknown")
    if not body.get("successful"):
        return _entry("down")
    ping_time = body.get("pingTime")
    return _entry("healthy", None, int(ping_time) if isinstance(ping_time, (int, float)) else None)


async def _probe_tcp(host: str, port: int) -> dict:
    """Probe a TCP service by opening (and immediately closing) a connection.

    Args:
        host (str): Target host name on the trust network.
        port (int): Target port.

    Returns:
        dict: Wire-shaped entry; ``down`` on refusal or timeout.
    """
    start = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), _PROBE_TIMEOUT_SECONDS)
    except Exception:
        return _entry("down")
    elapsed_ms = int((time.monotonic() - start) * 1000)
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass  # the connect already proved liveness; teardown noise is irrelevant
    return _entry(_status_from_latency(elapsed_ms), None, elapsed_ms)


async def collect_once(client: httpx.AsyncClient) -> dict:
    """Probe every roster service concurrently and assemble a heartbeat-ready snapshot.

    Args:
        client (httpx.AsyncClient): HTTP client shared across probes.

    Returns:
        dict: ``{"services": {<key>: entry, ...}, "collected_at": <iso-utc>}``.
    """
    imaging, data_access, xnat, dicom, omop = await asyncio.gather(
        _probe_health_endpoint(client, f"{IMAGING_API_URL}/health"),
        _probe_health_endpoint(client, f"{DATA_ACCESS_API_URL}/health"),
        _probe_xnat(client),
        _probe_dicom(client),
        _probe_tcp(OMOP_DB_HOST, OMOP_DB_PORT),
    )
    return {
        "services": {
            "trust-api": _entry("healthy", _own_version()),
            "imaging-api": imaging,
            "data-access-api": data_access,
            "xnat": xnat,
            "dicom": dicom,
            "omop": omop,
        },
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


async def run_health_collector() -> None:
    """Main collection loop. Runs indefinitely, refreshing the cached snapshot.

    This function is started as a background task during the FastAPI lifespan,
    alongside the task poller.
    """
    global _snapshot  # noqa: PLW0603
    logger.info(f"Starting health collector, probing trust services every {HEALTH_COLLECT_INTERVAL_SECONDS}s")

    async with httpx.AsyncClient(timeout=httpx.Timeout(_PROBE_TIMEOUT_SECONDS)) as client:
        while True:
            try:
                _snapshot = await collect_once(client)
            except Exception as e:
                logger.error(f"Error collecting service health: {e}")
            await asyncio.sleep(HEALTH_COLLECT_INTERVAL_SECONDS)
