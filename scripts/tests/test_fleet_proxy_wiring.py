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
"""Guards on what a trust can reach of a Flower SuperLink in the compose files (FLIP#1517).

flwr>=1.40 serves the SuperLink's Control API (unauthenticated in open-source Flower) and the
ServerApp's Runtime API on the same port as the Fleet API the SuperNodes need. So in every Flower
shape the SuperLink must sit where no trust can reach it, and the trust-facing name and port must
be the fleet proxy's. A one-line slip here (the SuperLink back on the trust network, or publishing
the trust port itself) hands every trust those APIs, and nothing but a manual end-to-end run would
notice. Each check below pins one shape's wiring:

- hub dev (``deploy/compose.development.flower.yml``): on ``fl-net-<N>`` the only hub service is
  ``fl-fleet-proxy-net-<N>``, answering to ``fl-server-net-<N>``; the SuperLink is on ``default``
  alone, where the proxy reaches it by an alias of its own.
- production harness (``deploy/compose.prod.flower.yml``): only the proxy publishes
  ``FL_SERVER_PORT``.
- standalone secure stack (``fl-services/flower/compose.secure.yml``): the SuperNodes are on
  ``flwr-trust-net`` alone, the SuperLink is not on it, and the proxy is the one service on both.

Stdlib-only and executable as a plain script, like the other guards in this directory
(``test_trust_kit_scripts.yml`` runs them with ``python <file>``); it reuses
``test_container_identity``'s line-based compose parser.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_container_identity import REPO_ROOT, Service, parse_services  # noqa: E402

HUB_DEV = REPO_ROOT / "deploy/compose.development.flower.yml"
HUB_PROD = REPO_ROOT / "deploy/compose.prod.flower.yml"
SECURE = REPO_ROOT / "fl-services/flower/compose.secure.yml"
NETS = (1, 2)


def _key_block(service: Service, key: str) -> list[str]:
    """The lines under a service's four-space-indented ``key:``, up to its next sibling key."""
    out: list[str] = []
    inside = False
    for line in service.lines:
        if re.match(rf"^ {{4}}{key}:\s*$", line):
            inside = True
            continue
        if inside:
            if re.match(r"^ {4}\S", line) or re.match(r"^ {0,3}\S", line):
                break
            out.append(line)
    return out


def networks(service: Service) -> dict[str, set[str]]:
    """Network name -> aliases, for both the list (``- net``) and map (``net:``) forms."""
    nets: dict[str, set[str]] = {}
    current: str | None = None
    in_aliases = False
    for line in _key_block(service, "networks"):
        if m := re.match(r"^ {6}- ([\w.-]+)\s*$", line):
            nets[m.group(1)] = set()
            current, in_aliases = None, False
        elif m := re.match(r"^ {6}([\w.-]+):(.*)$", line):
            current, in_aliases = m.group(1), False
            nets[current] = set()
        elif re.match(r"^ {8}aliases:\s*$", line):
            in_aliases = True
        elif in_aliases and current and (m := re.match(r"^ {10}- ([\w.-]+)\s*$", line)):
            nets[current].add(m.group(1))
    return nets


def ports(service: Service) -> list[str]:
    return [
        m.group(1).strip("\"'") for line in _key_block(service, "ports") if (m := re.match(r"^ {6}- (.+?)\s*$", line))
    ]


def env(service: Service) -> dict[str, str]:
    return dict(
        m.groups() for line in _key_block(service, "environment") if (m := re.match(r"^ {6}- (\w+)=(.*?)\s*$", line))
    )


def _by_name(path: Path) -> dict[str, Service]:
    return {s.name: s for s in parse_services(path)}


def check_hub_dev(failures: list[str]) -> None:
    services = _by_name(HUB_DEV)
    for n in NETS:
        server, proxy = services.get(f"fl-server-net-{n}"), services.get(f"fl-fleet-proxy-net-{n}")
        if server is None or proxy is None:
            failures.append(f"{HUB_DEV.name}: fl-server-net-{n} and fl-fleet-proxy-net-{n} must both be defined")
            continue
        on_trust_net = sorted(s.name for s in services.values() if f"fl-net-{n}" in networks(s))
        if on_trust_net != [proxy.name]:
            failures.append(f"{HUB_DEV.name}: fl-net-{n} must hold the fleet proxy alone, has {on_trust_net}")
        if f"fl-server-net-{n}" not in networks(proxy).get(f"fl-net-{n}", set()):
            failures.append(f"{proxy.where}: must answer to fl-server-net-{n} on fl-net-{n} (the kits' SAN)")
        if set(networks(server)) != {"default"}:
            failures.append(f"{server.where}: must be on `default` alone, is on {sorted(networks(server))}")
        upstream_alias = f"fl-superlink-net-{n}"
        if upstream_alias not in networks(server).get("default", set()):
            failures.append(f"{server.where}: needs the {upstream_alias} alias on `default` (the proxy's upstream)")
        if env(proxy).get("FLEET_PROXY_UPSTREAM") != f"{upstream_alias}:8000":
            failures.append(f"{proxy.where}: FLEET_PROXY_UPSTREAM must be {upstream_alias}:8000")


def check_hub_prod(failures: list[str]) -> None:
    services = _by_name(HUB_PROD)
    publishers = sorted(s.name for s in services.values() if any("FL_SERVER_PORT" in p for p in ports(s)))
    if publishers != ["fl-fleet-proxy-net-1"]:
        failures.append(f"{HUB_PROD.name}: only fl-fleet-proxy-net-1 may publish FL_SERVER_PORT, found {publishers}")
    server = services.get("fl-server-net-1")
    if server is not None and ports(server):
        failures.append(f"{server.where}: the SuperLink must publish no port, publishes {ports(server)}")


def check_secure(failures: list[str]) -> None:
    services = _by_name(SECURE)
    supernodes = [s for s in services.values() if s.name.startswith("supernode-")]
    if not supernodes:
        failures.append(f"{SECURE.name}: no supernode-* services parsed")
    for node in supernodes:
        if set(networks(node)) != {"flwr-trust-net"}:
            failures.append(f"{node.where}: must be on flwr-trust-net alone, is on {sorted(networks(node))}")
    both = sorted(s.name for s in services.values() if {"flwr-trust-net", "flwr-dev-net"} <= set(networks(s)))
    if both != ["fleet-proxy"]:
        failures.append(f"{SECURE.name}: only fleet-proxy may span both networks, found {both}")
    if "superlink" not in networks(services.get("fleet-proxy", Service("", SECURE))).get("flwr-trust-net", set()):
        failures.append(f"{SECURE.name}:fleet-proxy must answer to `superlink` on flwr-trust-net")
    superlink = services.get("superlink")
    if superlink is not None and "flwr-trust-net" in networks(superlink):
        failures.append(f"{superlink.where}: the SuperLink must not be on flwr-trust-net")


def main() -> int:
    failures: list[str] = []
    try:
        check_hub_dev(failures)
        check_hub_prod(failures)
        check_secure(failures)
    except ValueError as exc:
        failures.append(str(exc))

    if failures:
        print("❌ fleet proxy wiring guards failed:\n")
        for f in failures:
            print(f"  - {f}\n")
        return 1
    print("✅ fleet proxy wiring guards passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
