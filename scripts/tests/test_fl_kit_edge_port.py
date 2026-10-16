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

"""NVFLARE trust kits dial their own estate's server on its LZA edge port (FLIP#1365).

Production and staging share one edge NLB address: ``:8002`` forwards to production's FL server and
``:8003`` to staging's FL server, which listens on 8002. A client's ``connect_to.port`` (NVFLARE falls
back to the server's ``fed_learn_port`` when it is absent) therefore chooses the estate.

Flower's kit carries no address, so its SuperNode takes the port from the trust kit's ``FL_EDGE_PORT``.

Stdlib-only and executable as a plain script, like the rest of ``scripts/tests``.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROVISION = REPO / "fl-services" / "nvflare" / "provision"

# project file -> (server fed_learn_port, port every client must dial)
EXPECTED = {
    "net-1_project_prod.yml": (8002, 8002),
    "net-1_project_stag.yml": (8002, 8003),
}


def participants(path: Path) -> list[str]:
    """The text of each ``participants:`` entry in an NVFLARE project file."""
    body = re.search(r"^participants:\n(.*?)(?=^[A-Za-z])", path.read_text(), re.M | re.S)
    assert body, f"no participants block in {path}"
    return re.split(r"^  - ", body.group(1), flags=re.M)[1:]


def field(block: str, key: str, indent: int) -> str | None:
    """The scalar ``key`` at exactly ``indent`` spaces in ``block``, without quotes or a trailing comment."""
    match = re.search(rf"^ {{{indent}}}{key}:[ \t]*['\"]?([^'\"#\s]+)", block, re.M)
    return match.group(1) if match else None


def connect_to(entry: str) -> str:
    """The lines nested under an entry's ``connect_to:`` key, or empty when it has none."""
    match = re.search(r"^    connect_to:\n((?:(?: {6}.*)?\n)*)", entry, re.M)
    return match.group(1) if match else ""


class EdgePort(unittest.TestCase):
    def test_clients_dial_their_estates_server_on_its_edge_port(self):
        for name, (server_port, client_port) in EXPECTED.items():
            entries = participants(PROVISION / name)
            servers = [e for e in entries if field(e, "type", 4) == "server"]
            clients = [e for e in entries if field(e, "type", 4) == "client"]
            assert len(servers) == 1, name
            assert clients, name
            server = servers[0].split("\n", 1)[0].removeprefix("name: ").strip()
            assert field(servers[0], "fed_learn_port", 4) == str(server_port), name
            for client in clients:
                where = f"{name}: {client.splitlines()[0]}"
                block = connect_to(client)
                assert field(block, "host", 6) == server, where
                assert int(field(block, "port", 6) or server_port) == client_port, where

    def test_flower_supernode_dials_the_edge_port(self):
        compose = (REPO / "trust" / "deploy" / "compose_trust.production.flower.yml").read_text()
        assert "${NLB_SUBDOMAIN}:${FL_EDGE_PORT:-${FL_SERVER_PORT}}" in compose


if __name__ == "__main__":
    unittest.main()
