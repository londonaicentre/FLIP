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

import asyncio
import tomllib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import trust_api.services.health_collector as health_collector
from trust_api.services.health_collector import (
    _probe_dicom,
    _probe_health_endpoint,
    _probe_tcp,
    _probe_xnat,
    collect_once,
    current_snapshot,
    run_health_collector,
)

ROSTER = {"trust-api", "imaging-api", "data-access-api", "xnat", "dicom", "omop"}


@pytest.fixture(autouse=True)
def _reset_snapshot():
    """The collector caches the latest snapshot in a module global; reset per test."""
    health_collector._snapshot = None
    yield
    health_collector._snapshot = None


def _response(status_code=200, body=None, json_error=False):
    """Build a MagicMock httpx response with the given status and JSON body."""
    response = MagicMock()
    response.status_code = status_code
    response.is_success = 200 <= status_code < 300
    if json_error:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = body if body is not None else {}
    return response


# ---- _probe_health_endpoint (imaging-api / data-access-api /health) ----


@pytest.mark.asyncio
async def test_probe_health_endpoint_healthy_reports_latency_and_version():
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"status": "ok", "version": "0.3.0"})

    with patch("trust_api.services.health_collector.time.monotonic", side_effect=[10.0, 10.05]):
        result = await _probe_health_endpoint(mock_client, "http://imaging-api:8000/health")

    assert result == {"status": "healthy", "version": "0.3.0", "response_ms": 50}
    assert mock_client.get.call_args[0][0] == "http://imaging-api:8000/health"


@pytest.mark.asyncio
async def test_probe_health_endpoint_degraded_when_slower_than_threshold():
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"status": "ok", "version": "0.3.0"})

    with (
        patch.object(health_collector, "HEALTH_PROBE_DEGRADED_MS", 1000),
        patch("trust_api.services.health_collector.time.monotonic", side_effect=[10.0, 12.5]),
    ):
        result = await _probe_health_endpoint(mock_client, "http://imaging-api:8000/health")

    assert result["status"] == "degraded"
    assert result["response_ms"] == 2500


@pytest.mark.asyncio
async def test_probe_health_endpoint_down_on_transport_error():
    mock_client = AsyncMock()
    mock_client.get.side_effect = Exception("connection refused")

    result = await _probe_health_endpoint(mock_client, "http://imaging-api:8000/health")

    assert result == {"status": "down", "version": None, "response_ms": None}


@pytest.mark.asyncio
async def test_probe_health_endpoint_down_on_non_2xx():
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(500)

    result = await _probe_health_endpoint(mock_client, "http://imaging-api:8000/health")

    assert result == {"status": "down", "version": None, "response_ms": None}


@pytest.mark.asyncio
async def test_probe_health_endpoint_tolerates_missing_version():
    """Older service images answer {"status": "ok"} with no version — still healthy."""
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"status": "ok"})

    result = await _probe_health_endpoint(mock_client, "http://imaging-api:8000/health")

    assert result["status"] == "healthy"
    assert result["version"] is None


@pytest.mark.asyncio
async def test_probe_health_endpoint_tolerates_non_json_body():
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, json_error=True)

    result = await _probe_health_endpoint(mock_client, "http://imaging-api:8000/health")

    assert result["status"] == "healthy"
    assert result["version"] is None


@pytest.mark.asyncio
async def test_probe_health_endpoint_ignores_non_string_version():
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"status": "ok", "version": 42})

    result = await _probe_health_endpoint(mock_client, "http://imaging-api:8000/health")

    assert result["version"] is None


@pytest.mark.asyncio
async def test_probe_health_endpoint_tolerates_non_object_json_body():
    """A 2xx whose JSON body is not an object (proxy/gateway interposition) must
    degrade to version-less healthy, not raise out of the probe."""
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, ["not", "an", "object"])

    result = await _probe_health_endpoint(mock_client, "http://imaging-api:8000/health")

    assert result["status"] == "healthy"
    assert result["version"] is None


@pytest.mark.asyncio
async def test_probe_health_endpoint_truncates_oversized_version():
    """The hub rejects version > 64 chars — a weird sibling reply must not be able
    to 422 the whole heartbeat, so the collector clamps at the source."""
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"status": "ok", "version": "v" * 100})

    result = await _probe_health_endpoint(mock_client, "http://imaging-api:8000/health")

    assert result["version"] == "v" * 64


# ---- _probe_xnat (buildInfo) ----


@pytest.mark.asyncio
async def test_probe_xnat_healthy_parses_build_version():
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"version": "1.10.0", "buildNumber": "199"})

    with patch.object(health_collector, "XNAT_URL", "http://xnat-web:8080"):
        result = await _probe_xnat(mock_client)

    assert result["status"] == "healthy"
    assert result["version"] == "1.10.0"
    assert result["response_ms"] is not None
    assert mock_client.get.call_args[0][0] == "http://xnat-web:8080/xapi/siteConfig/buildInfo"


@pytest.mark.asyncio
async def test_probe_xnat_unauthorized_is_alive_without_version():
    """A 401/403 proves the servlet answered — treat as alive, just version-less
    (defensive against a future XNAT locking buildInfo behind auth)."""
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(401)

    result = await _probe_xnat(mock_client)

    assert result["status"] == "healthy"
    assert result["version"] is None
    assert result["response_ms"] is not None


@pytest.mark.asyncio
async def test_probe_xnat_down_on_5xx():
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(502)

    result = await _probe_xnat(mock_client)

    assert result == {"status": "down", "version": None, "response_ms": None}


@pytest.mark.asyncio
async def test_probe_xnat_down_on_transport_error():
    mock_client = AsyncMock()
    mock_client.get.side_effect = Exception("connection refused")

    result = await _probe_xnat(mock_client)

    assert result == {"status": "down", "version": None, "response_ms": None}


@pytest.mark.asyncio
async def test_probe_xnat_degraded_when_slow():
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"version": "1.10.0"})

    with (
        patch.object(health_collector, "HEALTH_PROBE_DEGRADED_MS", 1000),
        patch("trust_api.services.health_collector.time.monotonic", side_effect=[10.0, 11.5]),
    ):
        result = await _probe_xnat(mock_client)

    assert result["status"] == "degraded"


# ---- _probe_dicom (imaging-api ping_pacs deep probe) ----


@pytest.mark.asyncio
async def test_probe_dicom_healthy_uses_ping_time_and_internal_key():
    """The probe reuses trust-api's shared trust-internal header builder."""
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"successful": True, "pingTime": 31})

    with (
        patch.object(health_collector, "PACS_ID", 1),
        patch(
            "trust_api.services.health_collector._trust_internal_headers",
            return_value={"X-Trust-Internal-Service-Key": "secret"},
        ),
    ):
        result = await _probe_dicom(mock_client)

    assert result == {"status": "healthy", "version": None, "response_ms": 31}
    call_args = mock_client.get.call_args
    assert call_args[0][0].endswith("/imaging/ping_pacs/1")
    assert call_args[1]["headers"] == {"X-Trust-Internal-Service-Key": "secret"}


@pytest.mark.asyncio
async def test_probe_dicom_unknown_on_non_object_json_body():
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, "ok")

    result = await _probe_dicom(mock_client)

    assert result == {"status": "unknown", "version": None, "response_ms": None}


@pytest.mark.asyncio
async def test_probe_dicom_drops_non_finite_ping_time():
    """XNAT's pingTime is third-party data — NaN/Infinity must not raise or reach the wire."""
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"successful": True, "pingTime": float("nan")})

    result = await _probe_dicom(mock_client)

    assert result["status"] == "healthy"
    assert result["response_ms"] is None


@pytest.mark.asyncio
async def test_probe_dicom_clamps_out_of_range_ping_time():
    """The hub rejects response_ms outside [0, 1_000_000]; an out-of-range XNAT value
    must be clamped, not allowed to 422 every heartbeat."""
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"successful": True, "pingTime": 2_000_000})

    result = await _probe_dicom(mock_client)

    assert result["response_ms"] == 1_000_000


@pytest.mark.asyncio
async def test_probe_dicom_down_when_ping_unsuccessful():
    """A 200 with successful=false means XNAT reached out and the PACS did not answer."""
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"successful": False, "pingTime": None})

    result = await _probe_dicom(mock_client)

    assert result == {"status": "down", "version": None, "response_ms": None}


@pytest.mark.asyncio
async def test_probe_dicom_unknown_on_transport_error():
    """imaging-api unreachable — the prober chain is broken, not the PACS."""
    mock_client = AsyncMock()
    mock_client.get.side_effect = Exception("connection refused")

    result = await _probe_dicom(mock_client)

    assert result == {"status": "unknown", "version": None, "response_ms": None}


@pytest.mark.asyncio
async def test_probe_dicom_unknown_on_http_error():
    """A 5xx from imaging-api/XNAT proves nothing about the PACS itself."""
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(502)

    result = await _probe_dicom(mock_client)

    assert result == {"status": "unknown", "version": None, "response_ms": None}


@pytest.mark.asyncio
async def test_probe_dicom_unknown_on_non_json_body():
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, json_error=True)

    result = await _probe_dicom(mock_client)

    assert result == {"status": "unknown", "version": None, "response_ms": None}


# ---- _probe_tcp (omop-db) ----


@pytest.mark.asyncio
async def test_probe_tcp_healthy_on_connect():
    writer = MagicMock()
    writer.wait_closed = AsyncMock()

    async def fake_open_connection(host, port):
        return MagicMock(), writer

    with patch("trust_api.services.health_collector.asyncio.open_connection", side_effect=fake_open_connection):
        result = await _probe_tcp("omop-db", 5432)

    assert result["status"] == "healthy"
    assert result["version"] is None
    assert result["response_ms"] is not None
    writer.close.assert_called_once()


@pytest.mark.asyncio
async def test_probe_tcp_down_on_connection_error():
    with patch(
        "trust_api.services.health_collector.asyncio.open_connection",
        side_effect=ConnectionRefusedError("refused"),
    ):
        result = await _probe_tcp("omop-db", 5432)

    assert result == {"status": "down", "version": None, "response_ms": None}


@pytest.mark.asyncio
async def test_probe_tcp_down_on_timeout():
    async def never_connects(host, port):
        await asyncio.sleep(3600)

    with (
        patch("trust_api.services.health_collector.asyncio.open_connection", side_effect=never_connects),
        patch.object(health_collector, "_PROBE_TIMEOUT_SECONDS", 0.01),
    ):
        result = await _probe_tcp("omop-db", 5432)

    assert result == {"status": "down", "version": None, "response_ms": None}


@pytest.mark.asyncio
async def test_probe_tcp_stays_healthy_when_teardown_fails():
    """The connect already proved liveness — an RST on close must not flip the verdict
    (or, worse, escape and kill the whole collection cycle)."""
    writer = MagicMock()
    writer.close.side_effect = ConnectionResetError("RST")
    writer.wait_closed = AsyncMock()

    async def fake_open_connection(host, port):
        return MagicMock(), writer

    with patch("trust_api.services.health_collector.asyncio.open_connection", side_effect=fake_open_connection):
        result = await _probe_tcp("omop-db", 5432)

    assert result["status"] == "healthy"


# ---- collect_once ----


@pytest.mark.asyncio
async def test_collect_once_returns_exactly_the_roster_services():
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"status": "ok", "version": "0.3.0", "successful": True})

    with patch(
        "trust_api.services.health_collector.asyncio.open_connection",
        side_effect=ConnectionRefusedError("refused"),
    ):
        snapshot = await collect_once(mock_client)

    assert set(snapshot["services"].keys()) == ROSTER
    assert snapshot["collected_at"]  # ISO timestamp present


@pytest.mark.asyncio
async def test_collect_once_trust_api_entry_is_static_with_own_version():
    """trust-api is not probed — if this code runs, the service is up. Its status on the
    Connection Status page is derived hub-side from heartbeat age instead."""
    expected_version = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
    )["project"]["version"]

    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"status": "ok", "successful": True, "pingTime": 5})

    with patch(
        "trust_api.services.health_collector.asyncio.open_connection",
        side_effect=ConnectionRefusedError("refused"),
    ):
        snapshot = await collect_once(mock_client)

    assert snapshot["services"]["trust-api"] == {
        "status": "healthy",
        "version": expected_version,
        "response_ms": None,
    }


@pytest.mark.asyncio
async def test_collect_once_reports_down_omop_via_tcp():
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"status": "ok", "successful": True, "pingTime": 5})

    with patch(
        "trust_api.services.health_collector.asyncio.open_connection",
        side_effect=ConnectionRefusedError("refused"),
    ):
        snapshot = await collect_once(mock_client)

    assert snapshot["services"]["omop"]["status"] == "down"


@pytest.mark.asyncio
async def test_collect_once_isolates_a_probe_that_raises():
    """A bug escaping one probe must cost that entry (unknown), never the cycle —
    the other five services still report."""
    mock_client = AsyncMock()
    mock_client.get.return_value = _response(200, {"status": "ok", "successful": True, "pingTime": 5})

    with (
        patch(
            "trust_api.services.health_collector._probe_xnat",
            new_callable=AsyncMock,
            side_effect=TypeError("refactor bug"),
        ),
        patch(
            "trust_api.services.health_collector.asyncio.open_connection",
            side_effect=ConnectionRefusedError("refused"),
        ),
    ):
        snapshot = await collect_once(mock_client)

    assert set(snapshot["services"].keys()) == ROSTER
    assert snapshot["services"]["xnat"] == {"status": "unknown", "version": None, "response_ms": None}
    assert snapshot["services"]["imaging-api"]["status"] == "healthy"


# ---- current_snapshot / run_health_collector ----


def test_current_snapshot_is_none_before_first_collection():
    assert current_snapshot() is None


@pytest.mark.asyncio
async def test_run_health_collector_caches_snapshot_while_alive_and_clears_on_exit():
    seen_during_loop: list[dict | None] = []

    async def fake_sleep(seconds):
        # Observe the cache from inside the live loop (between cycles), then stop.
        seen_during_loop.append(current_snapshot())
        raise asyncio.CancelledError()

    with (
        patch(
            "trust_api.services.health_collector.collect_once",
            new_callable=AsyncMock,
            return_value={"services": {}, "collected_at": "now"},
        ) as mock_collect,
        patch("trust_api.services.health_collector.asyncio.sleep", side_effect=fake_sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_health_collector()

    mock_collect.assert_called_once()
    assert seen_during_loop == [{"services": {}, "collected_at": "now"}]
    # Exit (shutdown or death) clears the cache so the poller can't ride stale data.
    assert current_snapshot() is None


@pytest.mark.asyncio
async def test_run_health_collector_continues_on_collection_error():
    call_count = 0

    async def fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise asyncio.CancelledError()

    with (
        patch(
            "trust_api.services.health_collector.collect_once",
            new_callable=AsyncMock,
            side_effect=Exception("probe explosion"),
        ),
        patch("trust_api.services.health_collector.asyncio.sleep", side_effect=fake_sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_health_collector()

    assert current_snapshot() is None


@pytest.mark.asyncio
async def test_run_health_collector_clears_snapshot_when_the_loop_dies():
    """If the loop itself dies (scaffolding failure, cancellation), the cached
    snapshot must not survive it — a still-alive poller would otherwise keep
    attaching frozen data that the hub re-stamps as fresh."""
    with (
        patch(
            "trust_api.services.health_collector.collect_once",
            new_callable=AsyncMock,
            return_value={"services": {}, "collected_at": "t0"},
        ),
        patch(
            "trust_api.services.health_collector.asyncio.sleep",
            side_effect=RuntimeError("event loop scaffolding failure"),
        ),
    ):
        with pytest.raises(RuntimeError):
            await run_health_collector()

    assert current_snapshot() is None


@pytest.mark.asyncio
async def test_run_health_collector_drops_snapshot_when_a_later_cycle_fails():
    """A failing collector must NOT keep riding its last good snapshot: the hub
    re-stamps freshness on every heartbeat, so retaining stale data would render
    week-old statuses as current forever. Dropping to None makes the heartbeat go
    bodyless and the UI honestly report No data via its staleness window."""
    call_count = 0

    async def fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError()

    with (
        patch(
            "trust_api.services.health_collector.collect_once",
            new_callable=AsyncMock,
            side_effect=[{"services": {}, "collected_at": "t0"}, Exception("probe explosion")],
        ),
        patch("trust_api.services.health_collector.asyncio.sleep", side_effect=fake_sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_health_collector()

    assert current_snapshot() is None
