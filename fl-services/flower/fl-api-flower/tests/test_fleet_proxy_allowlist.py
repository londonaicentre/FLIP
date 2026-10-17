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
"""The fleet proxy lets through exactly flwr's Fleet API, and nothing else (FLIP#1517).

The proxy (fl-services/flower/fleet-proxy) is the only thing a trust reaches on the hub's
SuperLink: it forwards the routes in fleet-routes.map and answers everything else 404. These
tests read the routes from the flwr this service locks, so a Flower bump that adds, renames
or drops a Fleet route fails here rather than 404ing SuperNodes mid-round, and a Control or
Runtime route can never be allowlisted by accident. That only holds while this lock and the
SuperLink image carry the same flwr, which the last test pins; the rest pin the nginx details
the allowlist depends on.
"""

import os
import re
import tomllib
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from flwr.superlink.routers.fleet.router import router as fleet_router

FLOWER_DIR = Path(__file__).resolve().parents[2]
PROXY_DIR = FLOWER_DIR / "fleet-proxy"
_ENTRY = re.compile(r'^\s*"(?P<method>[A-Z]+) (?P<uri>\S+)"\s+1;\s*$')


def _read(path: Path) -> str:
    if not path.is_file():
        # The fl-api image mounts only this service's tests; the rest of the tree is a repo sibling.
        if os.environ.get("CI"):
            pytest.fail(f"{path} not found")
        pytest.skip(f"{path} not present (running outside a repo checkout)")
    return path.read_text()


@pytest.fixture(scope="module")
def routes_map() -> str:
    return _read(PROXY_DIR / "fleet-routes.map")


@pytest.fixture(scope="module")
def template() -> str:
    return _read(PROXY_DIR / "templates" / "fleet-proxy.conf.template")


@pytest.fixture(scope="module")
def allowlist(routes_map: str) -> set[tuple[str, str]]:
    """(method, uri) pairs the proxy forwards, parsed from its nginx map."""
    entries = {(m["method"], m["uri"]) for line in routes_map.splitlines() if (m := _ENTRY.match(line))}
    assert entries, "no allowlist entries parsed from fleet-routes.map"
    return entries


def _fleet_router_routes() -> set[tuple[str, str]]:
    routes = [route for route in fleet_router.routes if isinstance(route, APIRoute)]
    return {(method, route.path) for route in routes for method in route.methods or ()}


def test_allowlist_matches_flwr_fleet_router(allowlist):
    assert allowlist == _fleet_router_routes()


def test_allowlist_is_post_only_and_fleet_only(allowlist):
    assert {method for method, _ in allowlist} == {"POST"}
    assert all(uri.startswith("/v1/fleet/") and "?" not in uri for _, uri in allowlist)


def test_the_map_is_keyed_on_the_raw_request_uri(routes_map):
    # $uri is nginx's normalised path: a dot-segment or percent-encoded path would match an
    # entry there and still reach a Control or Runtime route behind it.
    assert 'map "$request_method $request_uri" $fleet_route_allowed {' in routes_map
    assert re.search(r"^\s*default 0;$", routes_map, re.MULTILINE)


def test_anything_off_the_allowlist_is_refused_before_any_location(template):
    server = template[template.index("server {") :]
    assert re.search(r"if \(\$fleet_route_allowed = 0\) \{\s*return 404;\s*\}", server)
    assert server.index("$fleet_route_allowed") < server.index("location /")


def test_the_upstream_is_verified_and_sent_the_request_unchanged(template):
    assert "proxy_ssl_verify on;" in template
    assert "proxy_ssl_trusted_certificate ${FLEET_PROXY_CERTS_DIR}/ca.crt;" in template
    # No URI part, so the client's (already matched) request URI goes upstream as-is.
    assert re.search(r"proxy_pass \$fleet_upstream;", template)


def test_an_unreachable_superlink_reads_as_retryable(template):
    # The SuperNode retries 503/504 and gives up on any other error status.
    assert "error_page 502 =503" in template


def test_every_template_variable_is_one_the_image_defines(template):
    dockerfile = _read(PROXY_DIR / "Dockerfile")
    used = set(re.findall(r"\$\{(\w+)\}", template))
    defined = set(re.findall(r"(?:^|\s)(FLEET_PROXY_\w+)=", dockerfile, re.MULTILINE))
    assert used <= defined, f"template variables with no image default: {sorted(used - defined)}"
    assert all(name.startswith("FLEET_PROXY_") for name in used)
    assert "NGINX_ENVSUBST_FILTER=^FLEET_PROXY_" in dockerfile


def test_this_lock_carries_the_superlink_images_flwr():
    lock = tomllib.loads(_read(FLOWER_DIR / "fl-api-flower" / "uv.lock"))
    locked = next(pkg["version"] for pkg in lock["package"] if pkg["name"] == "flwr")
    base = re.search(r"flwr/base:\$\{FLWR_VERSION:-([0-9.]+)-", _read(FLOWER_DIR / "fl-base" / "Dockerfile"))
    assert base, "fl-base/Dockerfile no longer defaults FLWR_VERSION to a flwr/base tag"
    assert locked == base.group(1), f"fl-api-flower locks flwr {locked}; the SuperLink image is {base.group(1)}"
    secure = set(re.findall(r"flwr/base:([0-9.]+)-", _read(FLOWER_DIR / "compose.secure.yml")))
    assert secure == {locked}, f"compose.secure.yml runs flwr/base {sorted(secure)}"
