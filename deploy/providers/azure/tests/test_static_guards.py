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
"""Static guards for deploy/providers/azure (FLIP#1390).

Payment safety and the no-IDs rule are enforced here, where no runtime test can see them:
no auto-approve, every provider pinned to var.subscription_id, no real GUIDs in tracked files,
and the node's NIC always behind its own NSG.
"""

from __future__ import annotations

import re

import pytest
from conftest import AZURE_DIR
from tf_blocks import hcl_block

MAKEFILE = AZURE_DIR / "Makefile"
TESTS_DIR = AZURE_DIR / "tests"
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
ROOTS = ["bootstrap", "vm"]
TARGETS = [
    "bootstrap",
    "init",
    "plan",
    "apply",
    "destroy",
    "destroy-bootstrap",
    "status",
    "reprovision",
    "selftest",
    "kit-upload",
    "selftest-kit",
    "logs",
    "report",
    "stop",
    "start",
    "test",
    "lint",
    "help",
]


def _tracked_files():
    suffixes = {".tf", ".hcl", ".tftpl", ".sh", ".md", ".py", ".yml", ""}
    return sorted(
        p
        for p in AZURE_DIR.rglob("*")
        if p.is_file() and ".terraform" not in p.parts and p.suffix in suffixes and p.name != ".terraform.lock.hcl"
    )


def test_discovery_finds_the_roots():
    for root in ROOTS:
        assert (AZURE_DIR / root / "versions.tf").exists(), root
    assert MAKEFILE in _tracked_files()


@pytest.mark.parametrize("target", TARGETS)
def test_makefile_has_target(target):
    assert re.search(rf"^{re.escape(target)}:", MAKEFILE.read_text(), re.M), f"missing target {target}"


def test_no_auto_approve_anywhere():
    # The guard's own tests name the flag, so they are not scanned.
    for path in _tracked_files():
        if TESTS_DIR in path.parents:
            continue
        assert "auto-approve" not in path.read_text(errors="ignore"), f"{path} must not skip Terraform's confirmation"


@pytest.mark.parametrize("root", ROOTS)
def test_provider_targets_the_named_subscription(root):
    text = (AZURE_DIR / root / "versions.tf").read_text()
    assert re.search(r"subscription_id\s*=\s*var\.subscription_id", text), (
        f"{root} must set subscription_id = var.subscription_id"
    )


def test_no_real_guids_in_tracked_files():
    for path in _tracked_files():
        for guid in GUID.findall(path.read_text(errors="ignore")):
            assert guid == "00000000-0000-0000-0000-000000000000", f"{path} contains a GUID: {guid}"


def test_every_az_call_names_the_subscription():
    lines = [line for line in MAKEFILE.read_text().splitlines() if re.search(r"\baz (vm|account show|storage)\b", line)]
    assert lines, "the Makefile drives az; the guard must see its calls"
    for line in lines:
        assert "--subscription" in line, f"az call without --subscription: {line.strip()}"


def test_no_inbound_security_rules_anywhere():
    for path in AZURE_DIR.rglob("*.tf"):
        text = path.read_text()
        assert "azurerm_network_security_rule" not in text, f"{path} adds an NSG rule"
        assert not re.search(r'direction\s*=\s*"Inbound"', text), f"{path} adds an inbound rule"


def test_the_nic_is_always_behind_its_own_nsg():
    main = (AZURE_DIR / "vm" / "main.tf").read_text()
    block = hcl_block(main, 'resource "azurerm_network_interface_security_group_association" "nic"')
    assert block, "the NIC must be associated with its own NSG"
    assert "count" not in block, "the association must exist in every mode"
    assert "for_each" not in block, "the association must exist in every mode"
    assert "azurerm_network_interface.node.id" in block
    assert "azurerm_network_security_group.nic.id" in block


def test_gitignore_hides_state_and_tfvars():
    ignored = (AZURE_DIR / ".gitignore").read_text()
    for pattern in ("*.tfvars", "*.tfstate", "**/.terraform/*"):
        assert pattern in ignored, pattern


def _recipe(target: str) -> str:
    match = re.search(rf"^{re.escape(target)}:[^\n]*\n((?:\t[^\n]*\n)+)", MAKEFILE.read_text(), re.M)
    assert match, f"no recipe for {target}"
    return match.group(1)


def test_apply_shows_the_plan_and_asks_before_applying():
    recipe = _recipe("apply")
    assert "show vm.tfplan" in recipe, "apply must show the saved plan first"
    assert "read -r" in recipe, "apply must wait for an answer: a saved plan applies without a prompt"
    assert '"yes"' in recipe, "apply must require a typed yes"
    assert recipe.index("read -r") < recipe.index("apply vm.tfplan"), "the question comes before the apply"


def test_bootstrap_budget_starts_this_month():
    assert "budget_start_date=$(shell date -u +%Y-%m-01T00:00:00Z)" in _recipe("bootstrap"), (
        "a monthly budget's start date must be in the current month, or bootstrap fails"
    )


def test_destroy_bootstrap_refuses_while_the_node_exists():
    recipe = _recipe("destroy-bootstrap")
    assert "az group exists" in recipe, "destroy-bootstrap must check for the node first"
    assert "--subscription" in recipe, "the check must name the subscription"
    assert recipe.index("az group exists") < recipe.index("destroy"), "the check comes before the destroy"


def test_bootstrap_keeps_its_region_and_puts_the_ip_with_the_node():
    recipe = _recipe("bootstrap")
    assert 'location=$(BOOTSTRAP_LOCATION)' in recipe, "bootstrap's own region must not move with the node"
    assert 'egress_location=$(LOCATION)' in recipe, "the outbound IP must sit in the node's region"



def test_plan_and_destroy_let_the_operator_through_the_kit_drop_firewall():
    text = MAKEFILE.read_text()
    for target in ("plan", "destroy"):
        body = text.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]
        assert "operator_ip=$(OPERATOR_IP)" in body, f"{target} must pass operator_ip"


def test_kit_upload_uses_entra_never_a_key():
    body = MAKEFILE.read_text().split("\nkit-upload:", 1)[1].split("\n\n", 1)[0]
    assert "--auth-mode login" in body, "kit-upload must authenticate with Entra (the kit drop has shared keys off)"
    assert "az storage blob upload" in body


def test_selftest_kit_packs_here_and_uploads_under_the_name_the_node_fetches():
    body = MAKEFILE.read_text().split("\nselftest-kit:", 1)[1].split("\n\n", 1)[0]
    assert "scripts/pack-selftest-kit.sh $(FL_BACKEND)" in body
    assert "NAME=selftest-$(FL_BACKEND).tar.gz" in body, "flip-node run-selftest fetches selftest-<backend>.tar.gz"


def test_packed_kits_are_never_committed():
    assert "build/" in (AZURE_DIR / ".gitignore").read_text().splitlines()
