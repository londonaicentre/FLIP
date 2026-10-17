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
import subprocess

import pytest
from conftest import AZURE_DIR
from tf_blocks import hcl_block

MAKEFILE = AZURE_DIR / "Makefile"
TESTS_DIR = AZURE_DIR / "tests"
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
ROOTS = ["bootstrap", "vm", "aks"]
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
    "join",
    "aks-init",
    "aks-plan",
    "aks-apply",
    "aks-destroy",
    "aks-credentials",
    "aks-stop",
    "aks-start",
    "aks-allow-ip",
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
    lines = [
        line for line in MAKEFILE.read_text().splitlines() if re.search(r"\baz (vm|account show|storage|aks)\b", line)
    ]
    assert lines, "the Makefile drives az; the guard must see its calls"
    for line in lines:
        assert "--subscription" in line, f"az call without --subscription: {line.strip()}"


def _security_rules(text: str) -> list[str]:
    rules, rest = [], text
    while (block := hcl_block(rest, "security_rule {")) != "":
        rules.append(block)
        rest = rest[rest.find(block) + len(block) :]
    return rules


def test_no_inbound_allow_rule_anywhere():
    for path in AZURE_DIR.rglob("*.tf"):
        text = path.read_text()
        assert "azurerm_network_security_rule" not in text, f"{path} adds a standalone NSG rule"
        for rule in _security_rules(text):
            if re.search(r'direction\s*=\s*"Inbound"', rule):
                assert re.search(r'access\s*=\s*"Deny"', rule), f"{path} allows inbound traffic:\n{rule}"


def test_the_nic_nsg_denies_all_inbound_above_azures_defaults():
    """A rule-less NSG still lets the VNet in (AllowVnetInBound, priority 65000)."""
    main = (AZURE_DIR / "vm" / "main.tf").read_text()
    rules = _security_rules(hcl_block(main, 'resource "azurerm_network_security_group" "nic"'))
    assert len(rules) == 1, rules
    for setting in (
        'direction                  = "Inbound"',
        'access                     = "Deny"',
        "priority                   = 4096",
        'source_address_prefix      = "*"',
        'destination_port_range     = "*"',
        'protocol                   = "*"',
    ):
        assert setting in rules[0], f"the NIC's deny rule lacks {setting!r}"


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


def test_bootstrap_keeps_its_own_region():
    recipe = _recipe("bootstrap")
    assert "location=$(BOOTSTRAP_LOCATION)" in recipe, "bootstrap's own region must not move with the node"


def test_only_the_created_networks_nat_gateway_owns_a_public_ip():
    # The hub admits a trust by its credentials, never by source IP (#199 added a trust IP, #251
    # removed it), and many trust estates forbid public IPs. The one exception is the NAT gateway
    # of a network this tree creates, which lives and dies with the node.
    owners = sorted(
        str(p.relative_to(AZURE_DIR))
        for p in AZURE_DIR.rglob("*.tf")
        if ".terraform" not in p.parts and 'resource "azurerm_public_ip"' in p.read_text()
    )
    assert owners == ["modules/network/main.tf"], owners
    nic = hcl_block((AZURE_DIR / "vm" / "main.tf").read_text(), 'resource "azurerm_network_interface" "node"')
    assert nic, "the node's NIC block was not found"
    assert "public_ip_address_id" not in nic, "the node's NIC must never carry a public IP"


def test_plan_lets_the_operator_through_the_kit_drop_firewall():
    # destroy passes a fixed placeholder instead: test_makefile_operator_ip.py
    body = MAKEFILE.read_text().split("\nplan:", 1)[1].split("\n\n", 1)[0]
    assert "operator_ip=$(OPERATOR_IP)" in body, "plan must pass operator_ip"


def test_kit_upload_uses_entra_never_a_key():
    body = MAKEFILE.read_text().split("\nkit-upload:", 1)[1].split("\n\n", 1)[0]
    assert "--auth-mode login" in body, "kit-upload must authenticate with Entra (the kit drop has shared keys off)"
    assert "az storage blob upload" in body
    assert "--no-progress" in body, "a progress bar floods Run Command and CI logs"


def test_selftest_kit_packs_here_and_uploads_under_the_name_the_node_fetches():
    body = MAKEFILE.read_text().split("\nselftest-kit:", 1)[1].split("\n\n", 1)[0]
    assert "scripts/pack-selftest-kit.sh $(FL_BACKEND)" in body
    assert "NAME=selftest-$(FL_BACKEND).tar.gz" in body, "flip-node run-selftest fetches selftest-<backend>.tar.gz"


def test_packed_kits_are_never_committed():
    assert "build/" in (AZURE_DIR / ".gitignore").read_text().splitlines()


def _target(name: str) -> str:
    return MAKEFILE.read_text().split(f"\n{name}:", 1)[1].split("\n\n", 1)[0]


def test_aks_apply_asks_first_and_refuses_while_the_vm_exists():
    body = _target("aks-apply")
    assert "show aks.tfplan" in body
    assert 'read -r -p "Apply this plan?' in body
    assert "az group exists" in body, "the VM holds the trial's vCPUs"
    assert "$(NODE_RG)" in body, "the VM holds the trial's vCPUs"


def test_aks_plan_admits_only_the_operator():
    # aks-destroy passes a fixed placeholder instead: test_makefile_aks_allowlist.py
    assert "operator_ip=$(OPERATOR_IP)" in _target("aks-plan")


def test_make_test_covers_the_aks_root():
    assert " aks;" in _target("test") or " aks " in _target("test")


def test_outbound_ip_reports_parse_in_bash():
    # A quote inside ${ip:-...} in a double-quoted echo is a bash syntax error that only shows
    # after a real apply, when the line finally runs.
    lines = [line.strip().lstrip("@") for line in MAKEFILE.read_text().splitlines() if "Outbound IP" in line]
    assert lines, "the apply targets print the outbound IP"
    for line in lines:
        script = line.replace("$$", "$").replace("$(TF)", "terraform")
        result = subprocess.run(["bash", "-n", "-c", script], capture_output=True, text=True)
        assert result.returncode == 0, f"{line}\n{result.stderr}"
