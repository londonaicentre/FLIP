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

"""Drift guard between the Amazon EKS node diagrams and the Terraform they depict.

The counterpart of ``../ec2/tests/test_architecture_diagram.py``. ``architecture/trust_eks.py`` maps each drawn
label to Terraform addresses; this suite checks every address exists in the root or in the network module it calls
(``../modules/network``, with ``count``, so as ``module.network[0].<address>``), that every address of this
root and every resource of a load-bearing type in the module is drawn. The ``.tf`` files are read as text: no
``terraform`` binary, no state, no credentials. The render smoke test needs graphviz and is skipped where ``dot`` is
absent.
"""

import functools
import re
import shutil
from pathlib import Path

import pytest
from architecture.trust_eks import DIAGRAMS, DRAWN_RESOURCE_TYPES, TERRAFORM_ADDRESSES, UNDRAWN_MODULES, render

ROOT_DIR = Path(__file__).resolve().parent.parent

#: Root ``module`` name -> its source directory, whose resources are addressed as ``module.<name>[0].<address>``.
MODULE_DIRS: dict[str, Path] = {"network": (ROOT_DIR / ".." / "modules" / "network").resolve()}

_RESOURCE_HEADER = re.compile(r'^resource\s+"([A-Za-z0-9_]+)"\s+"([A-Za-z0-9_-]+)"\s*\{', re.MULTILINE)
_DATA_HEADER = re.compile(r'^data\s+"([A-Za-z0-9_]+)"\s+"([A-Za-z0-9_-]+)"\s*\{', re.MULTILINE)
_MODULE_BLOCK = re.compile(r'^module\s+"([A-Za-z0-9_-]+)"\s*\{(.*?)^\}', re.MULTILINE | re.DOTALL)
_LOCAL_ADDRESS = r"(data\.[a-z0-9_]+\.[A-Za-z0-9_-]+|[a-z0-9_]+\.[A-Za-z0-9_-]+)"
_ADDRESS = re.compile(rf"module\.[A-Za-z0-9_-]+(\[0\]\.{_LOCAL_ADDRESS})?|{_LOCAL_ADDRESS}")
_MODULE_PREFIX = re.compile(r"^module\.[A-Za-z0-9_-]+\[0\]\.")


def _source(path: Path) -> str:
    """A ``.tf`` file with its comment lines dropped."""
    return "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#"))


@functools.cache
def _inventory(directory: Path) -> tuple[frozenset[str], dict[str, str]]:
    """Inventory one module directory.

    Args:
        directory (Path): A directory of ``.tf`` files.

    Returns:
        tuple[frozenset[str], dict[str, str]]: ``(resource_addresses, module_blocks)``: ``<type>.<name>`` for
            ``resource`` blocks, ``data.<type>.<name>`` for ``data`` blocks, and each ``module`` block's name mapped
            to its body.
    """
    resources: set[str] = set()
    modules: dict[str, str] = {}
    paths = sorted(directory.glob("*.tf"))
    assert paths, f"found no .tf files under {directory} — the inventory is broken, not the diagram"
    for path in paths:
        source = _source(path)
        resources.update(f"{kind}.{name}" for kind, name in _RESOURCE_HEADER.findall(source))
        resources.update(f"data.{kind}.{name}" for kind, name in _DATA_HEADER.findall(source))
        modules.update(_MODULE_BLOCK.findall(source))
    assert resources, f"inventory found no resource blocks in {directory} — the regex has drifted from the HCL"
    return frozenset(resources), modules


def _module_resources() -> set[str]:
    """Every resource address in the called modules, prefixed ``module.<name>[0].``."""
    return {
        f"module.{name}[0].{address}" for name, directory in MODULE_DIRS.items() for address in _inventory(directory)[0]
    }


def _drawn_addresses() -> set[str]:
    return {address for addresses in TERRAFORM_ADDRESSES.values() for address in addresses}


def test_module_dirs_match_the_root():
    """``MODULE_DIRS`` names exactly the root's ``module`` blocks, each called from that directory with ``count``.

    The ``[0]`` in the module addresses holds only while the call is counted; a ``for_each`` or an uncounted call
    would change every address, so it fails here first.
    """
    modules = _inventory(ROOT_DIR)[1]
    assert set(MODULE_DIRS) == set(modules)
    for name, body in modules.items():
        source = re.search(r'^\s*source\s*=\s*"([^"]+)"', body, re.MULTILINE)
        assert source, f"module {name!r} has no literal source"
        assert (ROOT_DIR / source[1]).resolve() == MODULE_DIRS[name], f"module {name!r} calls another directory"
        assert re.search(r"^\s*count\s*=", body, re.MULTILINE), f"module {name!r} is no longer counted"


def test_map_is_well_formed():
    """Every label maps to a tuple of Terraform addresses, none blank."""
    for label, addresses in TERRAFORM_ADDRESSES.items():
        assert label.strip(), "a diagram node has an empty label"
        assert isinstance(addresses, tuple), f"{label!r}: addresses must be a tuple, got {type(addresses).__name__}"
        for address in addresses:
            assert _ADDRESS.fullmatch(address), f"{label!r}: {address!r} is not a Terraform address"


def test_picture_stems_are_unique():
    """Every picture stem is unique: the Sphinx figure paths embed them."""
    stems = [suffix for suffix, _, _ in DIAGRAMS]
    assert len(stems) == len(set(stems)), f"duplicate picture suffixes in DIAGRAMS: {stems}"


def test_every_drawn_address_exists_in_terraform():
    """A node that names a resource or module the HCL no longer has is stale — fail, naming the node."""
    resources = set(_inventory(ROOT_DIR)[0]) | _module_resources()
    modules = {f"module.{name}" for name in _inventory(ROOT_DIR)[1]}
    missing = [
        f"{label!r} -> {address}"
        for label, addresses in TERRAFORM_ADDRESSES.items()
        for address in addresses
        if address not in resources and address not in modules
    ]
    assert not missing, (
        "diagram nodes reference Terraform that does not exist (renamed or removed? update TERRAFORM_ADDRESSES "
        "in architecture/trust_eks.py):\n  " + "\n  ".join(missing)
    )


def test_every_root_address_is_drawn():
    """Every resource, data source and module of this root appears in the map, whatever its type."""
    drawn = _drawn_addresses()
    root = set(_inventory(ROOT_DIR)[0]) | {f"module.{name}" for name in _inventory(ROOT_DIR)[1]}
    undrawn = sorted(root - drawn)
    assert not undrawn, (
        "Terraform addresses of this root are missing from the diagrams — add them to TERRAFORM_ADDRESSES in "
        "architecture/trust_eks.py:\n  " + "\n  ".join(undrawn)
    )


def test_every_load_bearing_module_resource_is_drawn():
    """Every module resource of a drawn type must appear in the map — a new one cannot land undrawn."""
    drawn = _drawn_addresses()
    undrawn = sorted(
        address
        for address in _module_resources()
        if _MODULE_PREFIX.sub("", address).partition(".")[0] in DRAWN_RESOURCE_TYPES and address not in drawn
    )
    assert not undrawn, (
        "network module resources of a drawn type are missing from the diagrams — add them to TERRAFORM_ADDRESSES "
        "in architecture/trust_eks.py (or drop the type from DRAWN_RESOURCE_TYPES with a rationale):\n  "
        + "\n  ".join(undrawn)
    )


def test_every_module_is_drawn_or_explicitly_exempted():
    """Every root ``module`` block is either drawn or listed in ``UNDRAWN_MODULES``."""
    modules = set(_inventory(ROOT_DIR)[1])
    drawn = {address.partition(".")[2] for address in _drawn_addresses() if re.fullmatch(r"module\.[\w-]+", address)}
    assert not sorted(modules - drawn - UNDRAWN_MODULES), "root modules neither drawn nor exempted"
    assert not sorted(UNDRAWN_MODULES - modules), "UNDRAWN_MODULES names modules that no longer exist"


def test_drawn_types_are_present_in_terraform():
    """A type in ``DRAWN_RESOURCE_TYPES`` that no resource uses is a typo or a stale rule."""
    addresses = set(_inventory(ROOT_DIR)[0]) | _module_resources()
    present = {_MODULE_PREFIX.sub("", address).partition(".")[0] for address in addresses}
    unused = sorted(DRAWN_RESOURCE_TYPES - present)
    assert not unused, f"DRAWN_RESOURCE_TYPES lists types with no resource: {unused}"


_NO_DOT = pytest.mark.skipif(
    shutil.which("dot") is None, reason="graphviz `dot` is not installed; the render cannot run here"
)


@_NO_DOT
def test_render_smoke(tmp_path: Path):
    """The pictures render: non-empty PNGs with the stems the Sphinx page embeds, every mapped node drawn."""
    outputs = render(tmp_path)
    assert {path.name for path in outputs} == {"trust-node-aws-eks-network.png", "trust-node-aws-eks-workload.png"}
    for path in outputs:
        assert path.stat().st_size > 0, f"{path} is empty"
