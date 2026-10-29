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

"""Drift guard between the Azure node diagrams and the Terraform they depict.

The counterpart of ``deploy/providers/AWS/tests/test_architecture_diagram.py``, over this tree's three roots and two
modules. ``architecture/trust_azure.py`` maps each drawn label to ``<directory>/<address>``; this suite checks every
address exists, that every resource of a load-bearing type in any directory is drawn, and that every ``module``
block is drawn or exempted. The ``.tf`` files are read as text: no ``terraform`` binary, no state, no credentials.
The render smoke test needs graphviz and is skipped where ``dot`` is absent.
"""

import functools
import re
import shutil
from pathlib import Path

import pytest
from architecture.trust_azure import DIAGRAMS, DRAWN_RESOURCE_TYPES, TERRAFORM_ADDRESSES, UNDRAWN_MODULES, render

AZURE_DIR = Path(__file__).resolve().parent.parent

#: Every directory of ``.tf`` files the pictures draw from: the three roots and the modules they call.
DIRECTORIES: tuple[str, ...] = ("bootstrap", "vm", "aks", "modules/network", "modules/kit_drop")

_RESOURCE_HEADER = re.compile(r'^resource\s+"([A-Za-z0-9_]+)"\s+"([A-Za-z0-9_-]+)"\s*\{', re.MULTILINE)
_DATA_HEADER = re.compile(r'^data\s+"([A-Za-z0-9_]+)"\s+"([A-Za-z0-9_-]+)"\s*\{', re.MULTILINE)
_MODULE_BLOCK = re.compile(r'^module\s+"([A-Za-z0-9_-]+)"\s*\{\s*source\s*=\s*"([^"]+)"', re.MULTILINE)
_ADDRESS = re.compile(
    r"(?P<dir>[a-z_]+(/[a-z_]+)?)/"
    r"(?P<address>module\.[A-Za-z0-9_-]+|data\.[a-z0-9_]+\.[A-Za-z0-9_-]+|[a-z0-9_]+\.[A-Za-z0-9_-]+)"
)


@functools.cache
def _inventory() -> tuple[frozenset[str], dict[str, str]]:
    """Inventory every directory in ``DIRECTORIES``.

    Returns:
        tuple[frozenset[str], dict[str, str]]: ``(addresses, modules)``. Addresses are ``<dir>/<type>.<name>`` and
            ``<dir>/data.<type>.<name>``; modules map ``<dir>/module.<name>`` to the module's directory relative to
            this tree.
    """
    addresses: set[str] = set()
    modules: dict[str, str] = {}
    for directory in DIRECTORIES:
        paths = sorted((AZURE_DIR / directory).glob("*.tf"))
        assert paths, f"found no .tf files under {directory} — the inventory is broken, not the diagram"
        for path in paths:
            source = "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#"))
            addresses.update(f"{directory}/{kind}.{name}" for kind, name in _RESOURCE_HEADER.findall(source))
            addresses.update(f"{directory}/data.{kind}.{name}" for kind, name in _DATA_HEADER.findall(source))
            for name, module_source in _MODULE_BLOCK.findall(source):
                target = (AZURE_DIR / directory / module_source).resolve().relative_to(AZURE_DIR)
                modules[f"{directory}/module.{name}"] = target.as_posix()
    assert addresses, "inventory found no resource blocks — the regex has drifted from the HCL"
    assert modules, "inventory found no module blocks — the regex has drifted from the HCL"
    return frozenset(addresses), modules


def _drawn_addresses() -> set[str]:
    return {address for addresses in TERRAFORM_ADDRESSES.values() for address in addresses}


def test_every_module_source_is_inventoried():
    """A ``module`` block calling a directory outside ``DIRECTORIES`` would escape the drift check."""
    _, modules = _inventory()
    outside = sorted(f"{block} -> {target}" for block, target in modules.items() if target not in DIRECTORIES)
    assert not outside, "module sources not in DIRECTORIES:\n  " + "\n  ".join(outside)


def test_map_is_well_formed():
    """Every label maps to a tuple of ``<directory>/<address>`` strings over a known directory."""
    for label, addresses in TERRAFORM_ADDRESSES.items():
        assert label.strip(), "a diagram node has an empty label"
        assert isinstance(addresses, tuple), f"{label!r}: addresses must be a tuple, got {type(addresses).__name__}"
        for address in addresses:
            match = _ADDRESS.fullmatch(address)
            assert match, f"{label!r}: {address!r} is not <directory>/<Terraform address>"
            assert match["dir"] in DIRECTORIES, f"{label!r}: {address!r} names an unknown directory"


def test_picture_stems_are_unique():
    """Every picture stem is unique: the Sphinx figure paths embed them."""
    stems = [suffix for suffix, _, _ in DIAGRAMS]
    assert len(stems) == len(set(stems)), f"duplicate picture suffixes in DIAGRAMS: {stems}"


def test_every_drawn_address_exists_in_terraform():
    """A node that names a resource or module the HCL no longer has is stale — fail, naming the node."""
    addresses, modules = _inventory()
    missing = [
        f"{label!r} -> {address}"
        for label, drawn in TERRAFORM_ADDRESSES.items()
        for address in drawn
        if address not in addresses and address not in modules
    ]
    assert not missing, (
        "diagram nodes reference Terraform that does not exist (renamed or removed? update TERRAFORM_ADDRESSES "
        "in architecture/trust_azure.py):\n  " + "\n  ".join(missing)
    )


def test_every_load_bearing_resource_is_drawn():
    """Every resource of a drawn type, in any root or module, must appear in the map."""
    addresses, _ = _inventory()
    drawn = _drawn_addresses()
    undrawn = sorted(
        address
        for address in addresses
        if address.rpartition("/")[2].partition(".")[0] in DRAWN_RESOURCE_TYPES and address not in drawn
    )
    assert not undrawn, (
        "Terraform resources of a drawn type are missing from the diagrams — add them to TERRAFORM_ADDRESSES in "
        "architecture/trust_azure.py (or drop the type from DRAWN_RESOURCE_TYPES with a rationale):\n  "
        + "\n  ".join(undrawn)
    )


def test_every_module_is_drawn_or_explicitly_exempted():
    """Every ``module`` block is either drawn or listed in ``UNDRAWN_MODULES`` — never silently skipped."""
    _, modules = _inventory()
    drawn = _drawn_addresses()
    assert not sorted(set(modules) - drawn - UNDRAWN_MODULES), "module blocks neither drawn nor exempted"
    assert not sorted(UNDRAWN_MODULES - set(modules)), "UNDRAWN_MODULES names modules that no longer exist"


def test_drawn_types_are_present_in_terraform():
    """A type in ``DRAWN_RESOURCE_TYPES`` that no resource uses is a typo or a stale rule."""
    addresses, _ = _inventory()
    present = {address.rpartition("/")[2].partition(".")[0] for address in addresses}
    unused = sorted(DRAWN_RESOURCE_TYPES - present)
    assert not unused, f"DRAWN_RESOURCE_TYPES lists types with no resource: {unused}"


_NO_DOT = pytest.mark.skipif(
    shutil.which("dot") is None, reason="graphviz `dot` is not installed; the render cannot run here"
)


@_NO_DOT
def test_render_smoke(tmp_path: Path):
    """The pictures render: non-empty PNGs with the stems the Sphinx page embeds, every mapped node drawn."""
    outputs = render(tmp_path)
    assert {path.name for path in outputs} == {"trust-node-azure-vm.png", "trust-node-azure-aks.png"}
    for path in outputs:
        assert path.stat().st_size > 0, f"{path} is empty"
