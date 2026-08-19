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
  static and the Connection Status page derives the row's status from heartbeat age
  instead.
- ``dicom`` is the PACS connector, probed transitively via imaging-api's ``ping_pacs``
  (imaging-api → XNAT → DIMSE echo). A transport/HTTP failure there proves nothing about
  the PACS itself, so it maps to ``unknown`` rather than ``down``.
- ``omop`` is a raw TCP connect — trust-api deliberately carries no Postgres driver.

Failure semantics: a probe can never raise out of ``collect_once`` (a leaked bug maps to
``unknown``), and a cycle that fails **or hangs** drops the cached snapshot entirely —
the heartbeat then goes bodyless, the hub's freshness stamp stops advancing, and the UI
honestly reports "No data" instead of re-rendering a frozen snapshot as current. The
hang case needs its own deadline because it raises nothing: without one the loop would
never refresh or drop the snapshot, which is the very failure this design prevents.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Literal

import httpx

from trust_api.config import get_settings
from trust_api.services.task_handlers import trust_internal_headers
from trust_api.utils.logger import logger
from trust_api.utils.version import service_version

DATA_ACCESS_API_URL = get_settings().DATA_ACCESS_API_URL
IMAGING_API_URL = get_settings().IMAGING_API_URL
XNAT_URL = get_settings().XNAT_URL
OMOP_DB_HOST = get_settings().OMOP_DB_HOST
OMOP_DB_PORT = get_settings().OMOP_DB_PORT
HEALTH_COLLECT_INTERVAL_SECONDS = get_settings().HEALTH_COLLECT_INTERVAL_SECONDS
HEALTH_PROBE_DEGRADED_MS = get_settings().HEALTH_PROBE_DEGRADED_MS

_PROBE_TIMEOUT_SECONDS = 5.0
_TEARDOWN_TIMEOUT_SECONDS = 1.0

# Whole-cycle deadline. Per-probe timeouts are not sufficient on their own: httpx's
# read timeout bounds the gap *between* chunks rather than total elapsed time, so a
# server dripping bytes (a wedged servlet behind a proxy) keeps a probe alive
# indefinitely without raising. An unbounded cycle would never refresh or drop the
# cached snapshot, and the poller would keep publishing frozen data that the hub
# re-stamps as fresh — so the cycle gets its own deadline and a hang is treated
# exactly like a failure.
_CYCLE_TIMEOUT_SECONDS = _PROBE_TIMEOUT_SECONDS * 3

# Version cap enforced by the hub (flip-api ServiceHealthEntry); applied in _entry so
# no reported version can 422 the whole snapshot. Latencies need no such clamp: the
# cycle deadline above bounds every measurement far below the hub's ceiling.
_MAX_VERSION_CHARS = 64

_ServiceStatus = Literal["healthy", "degraded", "down", "unknown"]

# Latest snapshot, replaced wholesale each collection cycle; None until the first
# cycle completes (and again after a failed cycle) so the heartbeat stays bodyless
# exactly like pre-collector builds whenever there is nothing trustworthy to report.
_snapshot: dict | None = None


def current_snapshot() -> dict | None:
    """Return the most recently collected health snapshot.

    Returns:
        dict | None: The heartbeat-ready body (``{"services": ..., "collected_at": ...}``),
        or None when the last collection cycle failed or none has completed yet.
    """
    return _snapshot


def _entry(status: _ServiceStatus, version: str | None = None, response_ms: int | None = None) -> dict:
    """Build one wire-shaped service entry.

    Args:
        status (_ServiceStatus): One of ``healthy | degraded | down | unknown``.
        version (str | None): Service version string when known.
        response_ms (int | None): Probe latency in milliseconds when measured.

    Returns:
        dict: ``{"status", "version", "response_ms"}``, with the version truncated
        to the hub's cap so no reported value can reject the whole snapshot.
    """
    return {
        "status": status,
        "version": version[:_MAX_VERSION_CHARS] if version is not None else None,
        "response_ms": response_ms,
    }


def _status_from_latency(response_ms: int) -> _ServiceStatus:
    """Map a successful probe's latency onto healthy/degraded.

    Args:
        response_ms (int): Measured probe latency in milliseconds.

    Returns:
        _ServiceStatus: ``"degraded"`` when slower than ``HEALTH_PROBE_DEGRADED_MS``,
        else ``"healthy"``.
    """
    return "degraded" if response_ms > HEALTH_PROBE_DEGRADED_MS else "healthy"


def _version_from_body(response: httpx.Response) -> str | None:
    """Extract a string ``version`` field from a JSON response body, tolerating anything.

    Args:
        response (httpx.Response): A successful probe response.

    Returns:
        str | None: The version string, or None when the body is not a JSON object
        or carries no usable version. ``_entry`` applies the hub's length cap.
    """
    # Broad except: the body is arbitrary bytes from another process — any parse
    # failure (non-JSON, bogus charset, non-object JSON) just means "no version".
    try:
        version = response.json().get("version")
    except Exception as e:
        logger.debug(f"No version in the response from {response.request.url}: {type(e).__name__}: {e}")
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
    except Exception as e:
        logger.warning(f"Health probe {url} failed: {type(e).__name__}: {e}")
        return _entry("down")
    if not response.is_success:
        logger.warning(f"Health probe {url} answered HTTP {response.status_code}")
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
    url = f"{XNAT_URL}/xapi/siteConfig/buildInfo"
    start = time.monotonic()
    try:
        response = await client.get(url)
    except Exception as e:
        logger.warning(f"Health probe {url} failed: {type(e).__name__}: {e}")
        return _entry("down")
    elapsed_ms = int((time.monotonic() - start) * 1000)
    if response.is_success:
        return _entry(_status_from_latency(elapsed_ms), _version_from_body(response), elapsed_ms)
    if response.status_code in (401, 403):
        return _entry(_status_from_latency(elapsed_ms), None, elapsed_ms)
    logger.warning(f"Health probe {url} answered HTTP {response.status_code}")
    return _entry("down")


async def _probe_dicom(client: httpx.AsyncClient) -> dict:
    """Probe the PACS connector via imaging-api's ``ping_pacs`` deep endpoint.

    Args:
        client (httpx.AsyncClient): HTTP client for making requests.

    Returns:
        dict: Wire-shaped entry. ``unknown`` when the prober chain (imaging-api → XNAT)
        itself fails, ``down`` only when XNAT reports the DIMSE echo failed.
    """
    # No id in the path: imaging-api resolves the registered PACS from XNAT. Pinning one here
    # made trust-api a second source of truth for an id XNAT assigns at registration — a
    # re-registered PACS reported permanently down while imports kept working (FLIP#993).
    url = f"{IMAGING_API_URL}/imaging/ping_pacs"
    start = time.monotonic()
    try:
        response = await client.get(url, headers=trust_internal_headers())
    except Exception as e:
        logger.warning(f"Health probe {url} failed: {type(e).__name__}: {e}")
        return _entry("unknown")
    elapsed_ms = int((time.monotonic() - start) * 1000)
    if not response.is_success:
        # A 401/403 here is a trust-internal key misconfiguration, not PACS weather —
        # log the status so the permanently-gray dot has a thread to pull.
        logger.warning(f"Health probe {url} answered HTTP {response.status_code}")
        return _entry("unknown")
    try:
        body = response.json()
    except Exception as e:
        logger.warning(f"Health probe {url} returned an unparseable body: {type(e).__name__}: {e}")
        return _entry("unknown")
    if not isinstance(body, dict):
        logger.warning(f"Health probe {url} returned {type(body).__name__}, expected a JSON object")
        return _entry("unknown")
    if not body.get("successful"):
        return _entry("down")

    # The response's pingTime is an epoch timestamp of the DIMSE echo, NOT a
    # duration (verified against live XNAT DQR) — report our own measured
    # round-trip of the whole imaging-api → XNAT → PACS echo chain instead.
    return _entry(_status_from_latency(elapsed_ms), None, elapsed_ms)


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
    except Exception as e:
        logger.warning(f"Health probe tcp://{host}:{port} failed: {type(e).__name__}: {e}")
        return _entry("down")
    elapsed_ms = int((time.monotonic() - start) * 1000)
    try:
        writer.close()
        # Bounded: wait_closed() can block indefinitely on a half-open connection,
        # and the except below catches raises, not hangs.
        await asyncio.wait_for(writer.wait_closed(), _TEARDOWN_TIMEOUT_SECONDS)
    except Exception:
        pass  # the connect already proved liveness; teardown noise is irrelevant
    return _entry(_status_from_latency(elapsed_ms), None, elapsed_ms)


def _entry_or_unknown(result: object, service: str) -> dict:
    """Map a gathered probe result onto a wire entry, absorbing leaked exceptions.

    Args:
        result (object): A probe's return value, or the exception it raised
            (``asyncio.gather(..., return_exceptions=True)``).
        service (str): Roster key, for the log line.

    Returns:
        dict: The probe's entry, or ``unknown`` when the probe raised or returned
        something that is not a wire entry.
    """
    if isinstance(result, dict):
        return result
    logger.error(f"Health probe for '{service}' returned no entry — {type(result).__name__}: {result}")
    return _entry("unknown")


async def collect_once(client: httpx.AsyncClient) -> dict:
    """Probe every roster service concurrently and assemble a heartbeat-ready snapshot.

    A single misbehaving probe costs its own entry (``unknown``), never the cycle —
    the other services still report.

    Args:
        client (httpx.AsyncClient): HTTP client shared across probes.

    Returns:
        dict: ``{"services": {<key>: entry, ...}, "collected_at": <iso-utc>}``.
    """
    results = await asyncio.gather(
        _probe_health_endpoint(client, f"{IMAGING_API_URL}/health"),
        _probe_health_endpoint(client, f"{DATA_ACCESS_API_URL}/health"),
        _probe_xnat(client),
        _probe_dicom(client),
        _probe_tcp(OMOP_DB_HOST, OMOP_DB_PORT),
        return_exceptions=True,
    )
    # strict: a sixth probe added without a sixth name would otherwise be dropped
    # silently while the five-way unpack still succeeded.
    imaging, data_access, xnat, dicom, omop = (
        _entry_or_unknown(result, service)
        for result, service in zip(results, ("imaging-api", "data-access-api", "xnat", "dicom", "omop"), strict=True)
    )
    return {
        "services": {
            "trust-api": _entry("healthy", service_version()),
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
    global _snapshot
    logger.info(f"Starting health collector, probing trust services every {HEALTH_COLLECT_INTERVAL_SECONDS}s")

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_PROBE_TIMEOUT_SECONDS)) as client:
            while True:
                # Drop the snapshot on any failure rather than keep riding a stale
                # one: the hub re-stamps freshness on every snapshot-carrying
                # heartbeat, so retaining old data would render it as current
                # indefinitely. A hang is a failure like any other — hence the
                # deadline, since a wedged probe raises nothing.
                try:
                    _snapshot = await asyncio.wait_for(collect_once(client), _CYCLE_TIMEOUT_SECONDS)
                except TimeoutError:
                    logger.error(
                        f"Health collection cycle exceeded {_CYCLE_TIMEOUT_SECONDS}s and was abandoned; "
                        "dropping snapshot until collection recovers"
                    )
                    _snapshot = None
                except Exception:
                    logger.exception("Error collecting service health; dropping snapshot until collection recovers")
                    _snapshot = None
                await asyncio.sleep(HEALTH_COLLECT_INTERVAL_SECONDS)
    finally:
        # The task is exiting (shutdown cancellation, or a scaffolding failure that
        # the lifespan's done-callback logs). A still-alive poller must not keep
        # attaching the last snapshot, so it dies with the loop.
        _snapshot = None
