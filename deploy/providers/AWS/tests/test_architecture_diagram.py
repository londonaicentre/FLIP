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

"""Drift guard between the Central Hub architecture diagram and the Terraform it depicts.

``architecture/central_hub.py`` draws the Central Hub from a hand-written map of node label ->
Terraform addresses (``TERRAFORM_ADDRESSES``). The picture is only worth publishing while that
map matches the HCL, so this suite pins the two together in both directions:

* every address the diagram claims to draw must exist as a ``resource`` or ``module`` block in
  the root module, so a renamed or removed resource fails here instead of leaving a stale box in
  the docs;
* every root-module resource of a load-bearing type (``DRAWN_RESOURCE_TYPES``), and every root
  module not explicitly exempted (``UNDRAWN_MODULES``), must appear in the map, so a new ECS
  service, bucket or load balancer cannot land without being drawn — or without an explicit
  decision not to.

The inventory is read straight from the ``.tf`` text with the same ``read_text()`` + ``re``
approach as the rest of this suite: no ``terraform`` binary, no state, no credentials, so it
runs on fork PRs. Only the root module is inventoried — ``module.<name>`` is the address of a
child module, and its internals are not separately drawn. The render itself needs graphviz, so
the smoke test is skipped (reported, not silently green) where ``dot`` is absent.
"""

import re
import shutil
from pathlib import Path

import pytest

from architecture.central_hub import (
    DRAWN_RESOURCE_TYPES,
    TERRAFORM_ADDRESSES,
    UNDRAWN_MODULES,
    render,
)

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent

_RESOURCE_HEADER = re.compile(r'^resource\s+"([A-Za-z0-9_]+)"\s+"([A-Za-z0-9_-]+)"\s*\{', re.MULTILINE)
_MODULE_HEADER = re.compile(r'^module\s+"([A-Za-z0-9_-]+)"\s*\{', re.MULTILINE)


def _root_module_sources() -> dict[Path, str]:
    """Return the text of every ``.tf`` file in the root module, comments stripped.

    Returns:
        dict[Path, str]: File path -> contents with ``#`` and ``//`` comment lines removed, so a
            block header that only appears inside a comment cannot satisfy an assertion.
    """
    sources = {}
    for path in sorted(AWS_PROVIDER_DIR.glob("*.tf")):
        lines = path.read_text().splitlines()
        sources[path] = "\n".join(line for line in lines if not line.lstrip().startswith(("#", "//")))
    assert sources, f"found no .tf files under {AWS_PROVIDER_DIR} — the inventory is broken, not the diagram"
    return sources


def _inventory() -> tuple[set[str], set[str]]:
    """Inventory the root module.

    Returns:
        tuple[set[str], set[str]]: ``(resource_addresses, module_names)`` where resource addresses
            are ``<type>.<name>`` and module names are the bare ``<name>`` of each ``module`` block.
    """
    resources: set[str] = set()
    modules: set[str] = set()
    for source in _root_module_sources().values():
        resources.update(f"{kind}.{name}" for kind, name in _RESOURCE_HEADER.findall(source))
        modules.update(_MODULE_HEADER.findall(source))
    assert resources, "inventory found no resource blocks — the regex has drifted from the HCL"
    assert modules, "inventory found no module blocks — the regex has drifted from the HCL"
    return resources, modules


def _drawn_addresses() -> set[str]:
    return {address for addresses in TERRAFORM_ADDRESSES.values() for address in addresses}


def test_map_is_well_formed():
    """Every label maps to a tuple of ``type.name`` / ``module.name`` strings, and no label is blank."""
    for label, addresses in TERRAFORM_ADDRESSES.items():
        assert label.strip(), "a diagram node has an empty label"
        assert isinstance(addresses, tuple), f"{label!r}: addresses must be a tuple, got {type(addresses).__name__}"
        for address in addresses:
            assert re.fullmatch(r"(module|[a-z0-9_]+)\.[A-Za-z0-9_-]+", address), (
                f"{label!r}: {address!r} is not a Terraform address of the form <type>.<name> or module.<name>"
            )


def test_every_drawn_address_exists_in_terraform():
    """A node that names a resource or module the HCL no longer has is stale — fail, naming the node."""
    resources, modules = _inventory()
    missing = []
    for label, addresses in TERRAFORM_ADDRESSES.items():
        for address in addresses:
            kind, _, name = address.partition(".")
            exists = name in modules if kind == "module" else address in resources
            if not exists:
                missing.append(f"{label!r} -> {address}")
    assert not missing, (
        "diagram nodes reference Terraform that does not exist in the root module "
        "(renamed or removed? update TERRAFORM_ADDRESSES in architecture/central_hub.py):\n  " + "\n  ".join(missing)
    )


def test_every_load_bearing_resource_is_drawn():
    """Every root-module resource of a drawn type must appear in the map — a new one cannot land undrawn."""
    resources, _ = _inventory()
    drawn = _drawn_addresses()
    undrawn = sorted(
        address for address in resources if address.partition(".")[0] in DRAWN_RESOURCE_TYPES and address not in drawn
    )
    assert not undrawn, (
        "Terraform resources of a drawn type are missing from the diagram — add them to TERRAFORM_ADDRESSES "
        "in architecture/central_hub.py (or, if they genuinely are not architecture, drop the type from "
        "DRAWN_RESOURCE_TYPES with a rationale):\n  " + "\n  ".join(undrawn)
    )


def test_every_module_is_drawn_or_explicitly_exempted():
    """Every root ``module`` block is either drawn or listed in ``UNDRAWN_MODULES`` — never silently skipped."""
    _, modules = _inventory()
    drawn = {address.partition(".")[2] for address in _drawn_addresses() if address.startswith("module.")}
    unaccounted = sorted(modules - drawn - UNDRAWN_MODULES)
    assert not unaccounted, (
        "root modules neither drawn nor exempted — add them to TERRAFORM_ADDRESSES or to UNDRAWN_MODULES "
        "in architecture/central_hub.py:\n  " + "\n  ".join(unaccounted)
    )
    stale_exemptions = sorted(UNDRAWN_MODULES - modules)
    assert not stale_exemptions, f"UNDRAWN_MODULES names modules that no longer exist: {stale_exemptions}"
    both = sorted(UNDRAWN_MODULES & drawn)
    assert not both, f"modules both drawn and exempted — pick one: {both}"


def test_drawn_types_are_present_in_terraform():
    """A type in ``DRAWN_RESOURCE_TYPES`` that no resource uses is a typo or a stale rule."""
    resources, _ = _inventory()
    present = {address.partition(".")[0] for address in resources}
    unused = sorted(DRAWN_RESOURCE_TYPES - present)
    assert not unused, f"DRAWN_RESOURCE_TYPES lists types with no resource in the root module: {unused}"


@pytest.mark.skipif(shutil.which("dot") is None, reason="graphviz `dot` is not installed; the render cannot run here")
def test_render_smoke(tmp_path: Path):
    """The script renders a non-empty PNG and draws every mapped node exactly once."""
    outputs = render(tmp_path)
    assert outputs, "render() returned no files"
    for path in outputs:
        assert path.suffix == ".png", f"{path} is not a PNG"
        assert path.stat().st_size > 0, f"{path} is empty"
