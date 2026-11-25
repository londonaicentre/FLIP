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
"""Reader of trust/deploy/endpoints.toml, the one list of what a trust node connects out to (FLIP#2076)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ENDPOINTS_FILE = Path(__file__).resolve().parents[1] / "endpoints.toml"
WHEN = ("always", "first-install", "provisioning")
PLATFORMS = ("", "azure-vm", "aks", "kubernetes")  # empty: every node


@dataclass(frozen=True)
class Endpoint:
    name: str
    when: str
    why: str
    hosts: tuple[str, ...] = ()
    host_from_kit: str = ""
    port: int | None = None
    port_from_kit: str = ""
    platform: str = ""  # empty: every node


def load(path: Path = ENDPOINTS_FILE) -> list[Endpoint]:
    """Every endpoint in the file. A row that is not one clear destination raises, naming the row."""
    endpoints: list[Endpoint] = []
    for row in tomllib.loads(path.read_text())["endpoint"]:
        try:
            endpoint = Endpoint(**{**row, "hosts": tuple(row.get("hosts", ()))})
        except TypeError as error:  # a key the schema does not have, or a missing one
            raise ValueError(f"{row.get('name', 'an unnamed endpoint')}: {error}") from error
        if endpoint.name in {e.name for e in endpoints}:
            raise ValueError(f"{endpoint.name}: named twice")
        if endpoint.when not in WHEN:
            raise ValueError(f"{endpoint.name}: when must be one of {', '.join(WHEN)}, not {endpoint.when!r}")
        if endpoint.platform not in PLATFORMS:
            raise ValueError(f"{endpoint.name}: platform must be one of {', '.join(PLATFORMS[1:])}")
        if bool(endpoint.host_from_kit) == ("hosts" in row):
            raise ValueError(f"{endpoint.name}: give either hosts or host_from_kit")
        if (endpoint.port is None) == (not endpoint.port_from_kit):
            raise ValueError(f"{endpoint.name}: give either port or port_from_kit")
        if endpoint.port is not None and not (isinstance(endpoint.port, int) and 0 < endpoint.port < 65536):
            raise ValueError(f"{endpoint.name}: port must be a number from 1 to 65535")
        endpoints.append(endpoint)
    return endpoints


def hosts(name: str, path: Path = ENDPOINTS_FILE) -> tuple[str, ...]:
    """The literal hosts of one named endpoint; an unknown name, or one with no literal host, raises."""
    found = next((e.hosts for e in load(path) if e.name == name), None)
    if not found:
        raise KeyError(f"{path.name} has no endpoint {name!r} with literal hosts")
    return found
