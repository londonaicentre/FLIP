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
"""The fleet proxy's allowlist is exactly flwr's Fleet API (FLIP#1517).

The proxy (fl-services/flower/fleet-proxy) is the only thing a trust reaches on the hub's
SuperLink: it forwards the routes in fleet-routes.map and answers everything else 404. These
tests read the routes from the flwr this service locks, so a Flower bump that adds, renames
or drops a Fleet route fails here rather than 404ing SuperNodes mid-round, and a Control or
Runtime route can never be allowlisted by accident.
"""

import inspect
import os
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from flwr.superlink.routers.fleet.router import router as fleet_router
from flwr.supernode.fleet_http_client import FleetHttpClient

ROUTES_MAP = Path(__file__).resolve().parents[2] / "fleet-proxy" / "fleet-routes.map"
_ENTRY = re.compile(r'^\s*"(?P<method>[A-Z]+) (?P<uri>\S+)"\s+1;\s*$')


@pytest.fixture(scope="module")
def allowlist() -> set[tuple[str, str]]:
    """(method, uri) pairs the proxy forwards, parsed from its nginx map."""
    if not ROUTES_MAP.is_file():
        # The fl-api image mounts only this service's tests; the proxy tree is a repo sibling.
        if os.environ.get("CI"):
            pytest.fail(f"fleet-proxy allowlist not found at {ROUTES_MAP}")
        pytest.skip("fleet-proxy tree not present (running outside a repo checkout)")
    entries = {(m["method"], m["uri"]) for line in ROUTES_MAP.read_text().splitlines() if (m := _ENTRY.match(line))}
    assert entries, f"no allowlist entries parsed from {ROUTES_MAP}"
    return entries


def _fleet_router_routes() -> set[tuple[str, str]]:
    routes = [route for route in fleet_router.routes if isinstance(route, APIRoute)]
    return {(method, route.path) for route in routes for method in route.methods or ()}


def _supernode_client_paths() -> set[str]:
    """Every path the SuperNode's Fleet client sends, captured by calling each RPC method."""
    sent: set[str] = set()

    class _Recorder(FleetHttpClient):
        def __init__(self) -> None:  # skip the real httpx client
            pass

        def _unary_unary(self, *, path: str, **_: object) -> None:  # type: ignore[override]
            sent.add(path)

    recorder = _Recorder()
    for name, member in inspect.getmembers(FleetHttpClient, inspect.isfunction):
        if name[0].isupper():  # RPC methods follow the protobuf service's CamelCase names
            member(recorder, None)
    return sent


def test_allowlist_matches_flwr_fleet_router(allowlist):
    assert allowlist == _fleet_router_routes()


def test_allowlist_covers_every_path_the_supernode_sends(allowlist):
    paths = _supernode_client_paths()
    assert paths, "captured no SuperNode Fleet paths; FleetHttpClient's shape changed"
    assert paths <= {uri for _, uri in allowlist}


def test_allowlist_is_post_only_and_fleet_only(allowlist):
    assert {method for method, _ in allowlist} == {"POST"}
    assert all(uri.startswith("/v1/fleet/") and "?" not in uri for _, uri in allowlist)
