# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Drift guard between the Snowflake node diagrams and the Terraform they depict.

The counterpart of ``../../azure/tests/test_architecture_diagram.py``, over the two Snowflake roots.
``architecture/trust_snowflake.py`` maps each drawn label to ``<root>/<address>``; this suite checks every address
exists and that every resource of a load-bearing type in either root is drawn. The ``.tf`` files are read as text: no
``tofu`` binary, no state, no credentials. The render smoke test needs graphviz and is skipped where ``dot`` is absent.
"""

import functools
import re
import shutil
from pathlib import Path

import pytest
from architecture.trust_snowflake import DIAGRAMS, DRAWN_RESOURCE_TYPES, TERRAFORM_ADDRESSES, render

TERRAFORM_DIR = Path(__file__).resolve().parent.parent.parent

#: Every directory of ``.tf`` files the pictures draw from: the platform tier and the workload tier.
ROOTS: tuple[str, ...] = ("snowflake-platform", "snowflake")

_RESOURCE_HEADER = re.compile(r'^resource\s+"([A-Za-z0-9_]+)"\s+"([A-Za-z0-9_-]+)"\s*\{', re.MULTILINE)
_MODULE_HEADER = re.compile(r'^module\s+"([A-Za-z0-9_-]+)"\s*\{', re.MULTILINE)
_ADDRESS = re.compile(r"(?P<root>[a-z-]+)/(?P<address>[a-z0-9_]+\.[A-Za-z0-9_-]+)")


@functools.cache
def _inventory() -> frozenset[str]:
    """Every resource in ``ROOTS``, as ``<root>/<type>.<name>``."""
    addresses: set[str] = set()
    for root in ROOTS:
        paths = sorted((TERRAFORM_DIR / root).glob("*.tf"))
        assert paths, f"found no .tf files under {root} — the inventory is broken, not the diagram"
        for path in paths:
            source = "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#"))
            addresses.update(f"{root}/{kind}.{name}" for kind, name in _RESOURCE_HEADER.findall(source))
            assert not _MODULE_HEADER.search(source), f"{path} calls a module: teach this inventory to follow it"
    assert addresses, "inventory found no resource blocks — the regex has drifted from the HCL"
    return frozenset(addresses)


def _drawn_addresses() -> set[str]:
    return {address for addresses in TERRAFORM_ADDRESSES.values() for address in addresses}


def _type(address: str) -> str:
    return address.partition("/")[2].partition(".")[0]


def test_map_is_well_formed():
    """Every label maps to a tuple of ``<root>/<address>`` strings over a known root."""
    for label, addresses in TERRAFORM_ADDRESSES.items():
        assert label.strip(), "a diagram node has an empty label"
        assert isinstance(addresses, tuple), f"{label!r}: addresses must be a tuple, got {type(addresses).__name__}"
        for address in addresses:
            match = _ADDRESS.fullmatch(address)
            assert match, f"{label!r}: {address!r} is not <root>/<Terraform address>"
            assert match["root"] in ROOTS, f"{label!r}: {address!r} names an unknown root"


def test_picture_stems_are_unique():
    """Every picture stem is unique: the Sphinx figure paths embed them."""
    stems = [suffix for suffix, _, _ in DIAGRAMS]
    assert len(stems) == len(set(stems)), f"duplicate picture suffixes in DIAGRAMS: {stems}"


def test_every_drawn_address_exists_in_terraform():
    """A node that names a resource the HCL no longer has is stale — fail, naming the node."""
    addresses = _inventory()
    missing = [
        f"{label!r} -> {address}"
        for label, drawn in TERRAFORM_ADDRESSES.items()
        for address in drawn
        if address not in addresses
    ]
    assert not missing, (
        "diagram nodes reference Terraform that does not exist (renamed or removed? update TERRAFORM_ADDRESSES "
        "in architecture/trust_snowflake.py):\n  " + "\n  ".join(missing)
    )


def test_every_load_bearing_resource_is_drawn():
    """Every resource of a drawn type, in either root, must appear in the map."""
    undrawn = sorted(a for a in _inventory() if _type(a) in DRAWN_RESOURCE_TYPES and a not in _drawn_addresses())
    assert not undrawn, (
        "Terraform resources of a drawn type are missing from the diagrams — add them to TERRAFORM_ADDRESSES in "
        "architecture/trust_snowflake.py (or drop the type from DRAWN_RESOURCE_TYPES with a rationale):\n  "
        + "\n  ".join(undrawn)
    )


def test_drawn_types_are_present_in_terraform():
    """A type in ``DRAWN_RESOURCE_TYPES`` that no resource uses is a typo or a stale rule."""
    unused = sorted(DRAWN_RESOURCE_TYPES - {_type(address) for address in _inventory()})
    assert not unused, f"DRAWN_RESOURCE_TYPES lists types with no resource: {unused}"


_NO_DOT = pytest.mark.skipif(
    shutil.which("dot") is None, reason="graphviz `dot` is not installed; the render cannot run here"
)


@_NO_DOT
def test_render_smoke(tmp_path: Path):
    """The pictures render: non-empty PNGs with the stems the Sphinx page embeds, every mapped node drawn."""
    outputs = render(tmp_path)
    assert {path.name for path in outputs} == {"trust-node-snowflake-account.png", "trust-node-snowflake-workload.png"}
    for path in outputs:
        assert path.stat().st_size > 0, f"{path} is empty"
