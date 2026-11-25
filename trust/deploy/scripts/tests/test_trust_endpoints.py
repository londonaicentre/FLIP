#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
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
"""trust/deploy/endpoints.toml is the one list of what a trust node connects out to (FLIP#2076).

These tests hold the reader to the file's rules and the docs tables to the file, so a host added
to one and not the other fails here rather than at a site's firewall.

Usage:
    uv run --no-config trust/deploy/scripts/tests/test_trust_endpoints.py
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trust_endpoints  # noqa: E402

DOCS = Path(__file__).resolve().parents[4] / "docs" / "source" / "deploy-flip"
# The example hub names the general table shows for the two endpoints whose host comes from the kit.
KIT_EXAMPLES = {"app.flip.aicentre.co.uk", "fl.app.flip.aicentre.co.uk"}
PASS = 0
FAIL = 0


def _assert(condition: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}")
        if detail:
            print(f"    {detail}")
        FAIL += 1


def _hosts(platform: str) -> set[str]:
    return {host for e in trust_endpoints.load() if e.platform == platform for host in e.hosts}


def _literals(text: str) -> set[str]:
    return set(re.findall(r"``([^`]+)``", text))


def _first_cells(page: str, heading: str) -> str:
    """The first cell of every row of the list-table under a heading: where a table names its destinations."""
    section = (DOCS / page).read_text().split(f"\n{heading}\n", 1)[1]
    table = section.split(".. list-table::", 1)[1]
    table = re.split(r"\n(?=\S)", table, maxsplit=1)[0]  # the table ends at the next unindented line
    cells = re.findall(r"^   \* - (.*(?:\n       .*)*)", table, flags=re.M)
    return "\n".join(cells[1:])  # the header row names no destination


def _rejects(row: str, says: str) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "endpoints.toml"
        path.write_text(f"[[endpoint]]\n{row}\n")
        try:
            trust_endpoints.load(path)
        except ValueError as error:
            return says in str(error)
    return False


def test_the_file_loads_and_names_each_endpoint_once() -> None:
    endpoints = trust_endpoints.load()
    names = [e.name for e in endpoints]
    _assert(len(names) == len(set(names)), "endpoint names are unique", str(names))
    always = {e.name for e in endpoints if e.when == "always" and not e.platform}
    _assert(always == {"hub-api", "fl-server", "image-registry"}, "a running node needs three endpoints", str(always))
    _assert(
        trust_endpoints.hosts("image-registry") == ("ghcr.io", "pkg-containers.githubusercontent.com"),
        "the registry is both hosts: ghcr.io alone fails every pull",
    )


def test_a_row_that_is_not_one_clear_destination_is_refused() -> None:
    base = 'name = "x"\nwhy = "y"\n'
    _assert(_rejects(base + 'when = "sometimes"\nhosts = ["a"]\nport = 1', "when must be"), "an unknown when")
    _assert(_rejects(base + 'when = "always"\nport = 1', "either hosts or host_from_kit"), "no host at all")
    _assert(
        _rejects(
            base + 'when = "always"\nhosts = ["a"]\nhost_from_kit = "K"\nport = 1', "either hosts or host_from_kit"
        ),
        "a literal host and a kit host together",
    )
    _assert(_rejects(base + 'when = "always"\nhosts = ["a"]', "either port or port_from_kit"), "no port")


def test_the_docs_tables_name_exactly_the_files_hosts() -> None:
    general = (
        _literals(_first_cells("deploy-trust-node.rst", "Network requirements\n====================")) - KIT_EXAMPLES
    )
    _assert(
        general == _hosts(""),
        "the general table",
        f"docs only: {general - _hosts('')}; file only: {_hosts('') - general}",
    )
    kubernetes = _literals(
        _first_cells("deploy-flip-node-kubernetes.rst", "Extra network egress\n====================")
    )
    _assert(kubernetes == _hosts("kubernetes"), "the Kubernetes extras table", str(kubernetes ^ _hosts("kubernetes")))
    azure = (DOCS / "deploy-flip-node-azure.rst").read_text().split("two first-boot extras on the VM:", 1)[1]
    azure = _literals(azure.split("**What leaves the site.**", 1)[0])
    _assert(azure == _hosts("azure-vm"), "the Azure VM first-boot extras", str(azure ^ _hosts("azure-vm")))


def main() -> None:
    test_the_file_loads_and_names_each_endpoint_once()
    test_a_row_that_is_not_one_clear_destination_is_refused()
    test_the_docs_tables_name_exactly_the_files_hosts()
    print("—")
    print(f"PASS={PASS}  FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
