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

"""Integration-test scaffolding for trust-api → data-access-api → omop-db (B3, issue #369).

Brings up a Compose stack containing a vanilla Postgres seeded with the OMOP fixture and
a freshly-built data-access-api, then runs trust-api code in-process. The handler under
test (``handle_cohort_query``) is invoked directly so the trust-api → data-access-api
hop is a real HTTP call — no ``make_request`` mocking. The downstream hub callback
(``POST /cohort/results``) is caught by an in-process stub HTTP server because B3 is
explicitly scoped to exclude flip-api involvement (that's the C-series job).

Conventions:

* The stack is session-scoped: building data-access-api once amortises uv-sync cost
  across the whole suite. Container ports are ephemeral; tests look them up via the
  ``data_access_api_url`` fixture instead of hard-coding.
* ``_patch_endpoints`` is autouse so every test gets the right URLs without ceremony.
  It rewrites the module-level constants captured at import time in ``task_handlers``
  rather than relying on env vars, because the trust-api conftest has already loaded
  Settings before this file runs.
"""

import http.server
import socketserver
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from testcontainers.compose import DockerCompose

from trust_api.services import task_handlers

# ---------------------------------------------------------------------------
# Compose stack
# ---------------------------------------------------------------------------


# Resolve the path to ``trust/`` from this file (``trust/trust-api/tests/integration/conftest.py``).
# DockerCompose's ``context`` arg is the directory that contains compose.test.yml.
_COMPOSE_DIR = Path(__file__).resolve().parents[3]
_COMPOSE_FILE = "compose.test.yml"
_DATA_ACCESS_SERVICE = "data-access-api-test"
_DATA_ACCESS_INTERNAL_PORT = 8000


@pytest.fixture(scope="session")
def compose_stack() -> Generator[DockerCompose, None, None]:
    """Start the omop-db + data-access-api Compose stack for the whole session.

    Compose-managed because the issue (#369) calls for a Compose-managed test fixture.
    Healthchecks defined inside the compose file gate readiness, so by the time
    ``DockerCompose.__enter__`` returns the data-access-api is reachable on its
    bound host port. Build cache is shared across runs — first cold build is slow,
    subsequent runs reuse the layers and the stack comes up in a few seconds.
    """
    with DockerCompose(
        context=str(_COMPOSE_DIR),
        compose_file_name=_COMPOSE_FILE,
        # Wait for the compose-level healthchecks rather than `wait_for_logs` —
        # logs are noisy and fragile across image versions.
        wait=True,
    ) as compose:
        yield compose


@pytest.fixture(scope="session")
def data_access_api_url(compose_stack: DockerCompose) -> str:
    """Resolve the ephemeral host URL where the data-access-api is reachable."""
    host, port = compose_stack.get_service_host_and_port(
        _DATA_ACCESS_SERVICE, _DATA_ACCESS_INTERNAL_PORT
    )
    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# Stub hub server — captures the trust-api → flip-api callback
# ---------------------------------------------------------------------------


class _StubHubHandler(http.server.BaseHTTPRequestHandler):
    """Minimal handler that records POST bodies and replies 200/JSON.

    ``handle_cohort_query`` discards the hub callback's response body, so any well-
    formed JSON keeps the handler's success path. Recording the bodies lets tests
    inspect what would have been sent to flip-api (B3 is scoped *not* to validate
    the hub side, but the visibility is useful for diagnosing failures).

    The handler also enforces the trust-api auth header. Without that check, a
    regression that drops ``TRUST_API_KEY_HEADER`` from the outbound request would
    silently pass — the stub would just record a header-less POST. Validating the
    header here means a missing-auth regression surfaces as ``make_request`` raising
    on the 401 response, which then surfaces as ``handle_cohort_query`` returning
    ``success=False`` and the test failing.
    """

    received: list[dict[str, Any]] = []
    # Populated at session-fixture setup time by ``stub_hub_server`` so the handler
    # has access to the same key/header pair the trust-api side uses on outbound
    # POSTs. Empty defaults make the handler safe to import without a fixture run.
    expected_header_name: str = ""
    expected_value: str = ""

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        # Auth gate before recording — a request that fails auth never lands in
        # ``received`` so tests can still ``assert stub_hub_received == []`` on
        # the failure-path branches without false positives.
        sent = self.headers.get(self.expected_header_name) if self.expected_header_name else None
        if not self.expected_value or sent != self.expected_value:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail": "missing or invalid trust-api key"}')
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else b""
        _StubHubHandler.received.append({
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body": body.decode("utf-8", errors="replace"),
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"message": "ok"}')

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — match stdlib signature
        # Silence the default per-request stderr line; pytest captures stdout already.
        return


@pytest.fixture(scope="session")
def stub_hub_server() -> Generator[socketserver.TCPServer, None, None]:
    """Run a stdlib HTTP server on an ephemeral localhost port for the session."""
    # Tell the handler which auth header / value to enforce. Reading from
    # ``task_handlers`` rather than hard-coding keeps the fixture in lock-step with
    # whatever the trust-api side actually sends — a rename of TRUST_API_KEY_HEADER
    # would not silently break the auth-gate.
    _StubHubHandler.expected_header_name = task_handlers.TRUST_API_KEY_HEADER
    _StubHubHandler.expected_value = task_handlers.TRUST_API_KEY
    server = socketserver.TCPServer(("127.0.0.1", 0), _StubHubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def stub_hub_received() -> Generator[list[dict[str, Any]], None, None]:
    """Per-test view of stub-hub captures, cleared on entry so tests don't leak."""
    _StubHubHandler.received.clear()
    yield _StubHubHandler.received
    _StubHubHandler.received.clear()


# ---------------------------------------------------------------------------
# Wire the trust-api side to the running stack
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_endpoints(
    data_access_api_url: str,
    stub_hub_server: socketserver.TCPServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rewrite the module-level URLs ``task_handlers`` captured at import time.

    The trust-api conftest ran first and populated Settings with placeholder values
    (``http://localhost:8001`` and friends). Resetting the env vars now would not help
    because Settings is a cached singleton, and ``task_handlers`` already pulled the
    URLs into module globals. Patching those globals is the smallest change that
    routes the in-process trust-api at the actual containers and stub hub.
    """
    # ``server_address`` is typed as a generic socket address (str | bytes for the host).
    # AF_INET always yields a (str, int) tuple, but typeshed widens it; coerce to str so
    # the f-string below doesn't accidentally render as ``b'127.0.0.1'``.
    hub_host = str(stub_hub_server.server_address[0])
    hub_port = stub_hub_server.server_address[1]
    monkeypatch.setattr(task_handlers, "DATA_ACCESS_API_URL", data_access_api_url)
    monkeypatch.setattr(
        task_handlers, "CENTRAL_HUB_API_URL", f"http://{hub_host}:{hub_port}"
    )
