#!/usr/bin/env python3
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
"""Every host port the composes publish binds to loopback unless the operator opts out.

A trust host promises no inbound, yet the trust composes published Orthanc, Grafana, the OMOP
database, Loki (which has no authentication of its own) and the three trust APIs on every
interface, and the hub composes did the same with Keycloak, the databases and the
unauthenticated FL API. Each published port now carries a ``${…_BIND_ADDRESS:-127.0.0.1}:``
prefix; the only ports left on every interface are the FL server's, which trusts dial.

Stdlib-only and executable as a plain script, like the rest of ``scripts/tests``.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HUB_COMPOSES = (
    "deploy/compose.development.yml",
    "deploy/compose.prod.yml",
    "deploy/compose.development.nvflare.yml",
    "deploy/compose.development.flower.yml",
    "deploy/compose.prod.nvflare.yml",
    "deploy/compose.prod.flower.yml",
)
TRUST_COMPOSES = (
    "trust/deploy/compose/compose_trust.prod.yml",
    "trust/deploy/compose/compose_trust.development.yml",
    "trust/deploy/compose/compose_trust-1_override.yml",
)
# The FL data plane: trusts connect to it from outside the host, so it stays on every interface.
PUBLIC_PORT_VARS = frozenset({"FL_SERVER_PORT", "FL_EDGE_PORT"})
PORT_ENTRY = re.compile(r"^\s*- \"?(?P<spec>[^\"#]+?)\"?\s*$")
LOOPBACK = re.compile(r"^(127\.0\.0\.1|\$\{[A-Z_]+BIND_ADDRESS:-127\.0\.0\.1\}):")


def published_ports(path: Path) -> list[str]:
    """The entries under every ``ports:`` key, as written."""
    entries, in_ports = [], False
    for line in path.read_text().splitlines():
        if re.match(r"^\s*ports:\s*$", line):
            in_ports = True
            continue
        if in_ports:
            if re.match(r"^\s*#", line):
                continue
            match = PORT_ENTRY.match(line)
            if not match:
                in_ports = False
                continue
            entries.append(match.group("spec"))
    return entries


class ComposeBindAddress(unittest.TestCase):
    def check(self, files: tuple[str, ...], var: str) -> None:
        for rel in files:
            for spec in published_ports(REPO / rel):
                with self.subTest(file=rel, port=spec):
                    port_var = re.match(r"\$\{([A-Z_]+)", spec.split(":")[-2])
                    if port_var and port_var.group(1) in PUBLIC_PORT_VARS:
                        assert not LOOPBACK.match(spec), f"{spec}: the FL port must stay reachable by trusts"
                        continue
                    # Either the tree's opt-out variable or a literal loopback (debug and control ports).
                    assert LOOPBACK.match(spec), f"{spec}: publish on loopback via ${{{var}:-127.0.0.1}}"

    def test_hub_composes(self) -> None:
        self.check(HUB_COMPOSES, "HUB_BIND_ADDRESS")

    def test_trust_composes(self) -> None:
        self.check(TRUST_COMPOSES, "TRUST_BIND_ADDRESS")

    def test_nvflare_harness_debug_port_on_loopback(self) -> None:
        # The standalone FLARE harness publishes its FL API as the hub composes did; only the debugger is pinned.
        debug = [s for s in published_ports(REPO / "fl-services/nvflare/compose.dev.yml") if "DEBUG_PORT" in s]
        assert debug, "fl-services/nvflare/compose.dev.yml no longer publishes a debug port"
        for spec in debug:
            assert LOOPBACK.match(spec), f"{spec}: a debugpy port must publish on 127.0.0.1"

    def test_parser_sees_the_ports(self) -> None:
        assert len(published_ports(REPO / "trust/deploy/compose/compose_trust.prod.yml")) >= 7
        assert len(published_ports(REPO / "deploy/compose.development.yml")) >= 5


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=2))
