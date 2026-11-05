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
"""Static checks for the AzureTRE workspace service bundle (FLIP#1792).

The bundle can only be deployed on an AzureTRE, so these pin what can be checked without one: the
manifest, schema and parameter set agree with each other and with the Terraform, the firewall
rules cover FLIP's documented egress, and nothing tracked here skips a confirmation or names a
real Azure ID.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

BUNDLE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BUNDLE_DIR.parents[2]
TERRAFORM_DIR = BUNDLE_DIR / "terraform"
DOCS = REPO_ROOT / "docs" / "source" / "deploy-flip"
SUBNET_TOKEN = "{{ resource.properties.address_space }}"
HUB_TOKEN = "{{ resource.properties.hub_api_hostname }}"
FL_IPS_TOKEN = "{{ resource.properties.fl_server_ip_addresses }}"
FL_PORT_TOKEN = "{{ resource.properties.fl_port }}"
HOST = re.compile(r"``([a-z0-9*][a-z0-9.*-]*\.[a-z]+)``")
FLIP_HUBS = ["app.flip.aicentre.co.uk", "stag.flip.aicentre.co.uk"]
UBUNTU_MIRRORS = {"azure.archive.ubuntu.com", "archive.ubuntu.com", "security.ubuntu.com"}


def _porter() -> dict:
    return yaml.safe_load((BUNDLE_DIR / "porter.yaml").read_text())


def _schema() -> dict:
    return json.loads((BUNDLE_DIR / "template_schema.json").read_text())


def _parameters() -> dict:
    return json.loads((BUNDLE_DIR / "parameters.json").read_text())


def _firewall_step(action: str) -> dict:
    steps = [s for s in _schema()["pipeline"][action] if s.get("resourceTemplateName") == "tre-shared-service-firewall"]
    assert len(steps) == 1, f"{action} needs exactly one firewall step"
    return steps[0]


def _collection(action: str, name: str) -> dict:
    return next(p["value"] for p in _firewall_step(action)["properties"] if p["name"] == name)


def _app_rules() -> list[dict]:
    return _collection("install", "rule_collections")["rules"]


def _network_rules() -> list[dict]:
    return _collection("install", "network_rule_collections")["rules"]


def _https_fqdns() -> set[str]:
    return {
        fqdn
        for rule in _app_rules()
        if {"port": "443", "type": "Https"} in rule["protocols"]
        for fqdn in rule["target_fqdns"]
    }


def _tf_names(kind: str) -> set[str]:
    text = "\n".join(p.read_text() for p in TERRAFORM_DIR.glob("*.tf"))
    return set(re.findall(rf'^{kind} "(\w+)"', text, re.MULTILINE))


def _egress_rows() -> list[tuple[str, str]]:
    """(destination, port) for each row of the list-table under deploy-trust-node-network."""
    text = (DOCS / "deploy-trust-node.rst").read_text()
    section = text.split(".. _deploy-trust-node-network:", 1)[1]
    table = section.split(".. list-table::", 1)[1].split("\n\n*Measured*", 1)[0]
    rows: list[list[str]] = []
    for line in table.splitlines():
        stripped = line.strip()
        if stripped.startswith("* - "):
            rows.append([stripped[4:]])
        elif stripped.startswith("- ") and rows:
            rows[-1].append(stripped[2:])
        elif stripped and rows:
            rows[-1][-1] += " " + stripped
    return [(row[0], row[1]) for row in rows[1:]]


def _first_boot_hosts() -> set[str]:
    """The first-boot extras the Azure VM page lists (the node installs the way that VM does)."""
    text = (DOCS / "deploy-flip-node-azure.rst").read_text()
    block = text.split("first-boot extras", 1)[1].split("\n\n", 2)[1]
    return set(HOST.findall(block))


def _tracked_files() -> list[Path]:
    skipped = {".terraform", "__pycache__", ".pytest_cache"}
    return sorted(
        p
        for p in BUNDLE_DIR.rglob("*")
        if p.is_file() and not skipped & set(p.parts) and p.name != ".terraform.lock.hcl"
    )


def test_json_files_parse():
    assert _schema()["pipeline"]
    assert _parameters()["schemaType"] == "ParameterSet"


def test_porter_manifest_names_the_bundle():
    porter = _porter()
    assert porter["name"].startswith("tre-service-")
    assert re.fullmatch(r"\d+\.\d+\.\d+", porter["version"])
    assert porter["dockerfile"] == "Dockerfile.tmpl"
    assert _parameters()["name"] == porter["name"], "AzureTRE's bundle-check-params requires the same name"


def test_parameter_set_matches_porter_parameters():
    # The rule AzureTRE's `make bundle-check-params` enforces at build time.
    porter = {p["name"] for p in _porter()["parameters"]} - {"arm_use_msi"}
    assert {p["name"] for p in _parameters()["parameters"]} - {"arm_use_msi"} == porter


@pytest.mark.parametrize("action", ["install", "upgrade", "uninstall"])
def test_every_action_passes_every_terraform_variable(action):
    (step,) = _porter()[action]
    assert set(step["terraform"]["vars"]) == _tf_names("variable")
    assert step["terraform"]["backendConfig"]["key"] == "${ bundle.name }-${ bundle.parameters.id }"


def test_porter_outputs_come_from_terraform():
    declared = {o["name"] for o in _porter()["outputs"]}
    assert declared <= _tf_names("output")
    for action in ("install", "upgrade"):
        (step,) = _porter()[action]
        assert {o["name"] for o in step["terraform"]["outputs"]} == declared


def test_vm_sizes_match_the_schema():
    sizes = _porter()["custom"]["vm_sizes"]
    prop = _schema()["properties"]["vm_size"]
    assert prop["enum"] == list(sizes)
    assert prop["default"] == next(p["default"] for p in _porter()["parameters"] if p["name"] == "vm_size")


def test_address_space_is_only_ever_allocated_by_the_tre():
    # The TRE API allocates a range only for a template whose schema has address_space, and replaces
    # any given value unless address_space_size is "custom". AzureTRE rejects properties a template
    # does not declare, so leaving address_space_size out means no one can ask for "custom": every
    # firewall rule's source is a range the TRE chose.
    schema = _schema()
    prop = schema["properties"]["address_space"]
    assert "address_space_size" not in schema["properties"]
    assert '"custom"' not in (BUNDLE_DIR / "template_schema.json").read_text()
    assert prop.get("updateable", False) is False
    assert schema["uiSchema"]["address_space"] == {"classNames": "tre-hidden"}
    assert schema["pipeline"]["install"][0]["resourceType"] == "workspace"


def test_firewall_opens_before_the_vm_boots():
    ids = [s["stepId"] for s in _schema()["pipeline"]["install"]]
    assert ids.index(_firewall_step("install")["stepId"]) < ids.index("main"), "first boot clones FLIP from GitHub"


def test_step_ids_are_unique():
    ids = [s["stepId"] for steps in _schema()["pipeline"].values() for s in steps if s["stepId"] != "main"]
    assert len(ids) == len(set(ids))


def test_upgrade_applies_the_same_rules_and_uninstall_removes_them():
    for name in ("rule_collections", "network_rule_collections"):
        assert _collection("upgrade", name) == _collection("install", name)
        assert _collection("uninstall", name) == {"name": _collection("install", name)["name"]}


def test_every_rule_is_scoped_to_the_node_subnet():
    for rule in _app_rules() + _network_rules():
        assert rule["source_addresses"] == [SUBNET_TOKEN], rule["name"]


def test_egress_table_parses():
    rows = _egress_rows()
    assert len(rows) >= 8, rows
    assert any(port == "443" for _, port in rows)


def test_app_rules_cover_every_https_host_in_the_egress_table():
    fqdns = _https_fqdns()
    for destination, port in _egress_rows():
        if port != "443":
            continue
        if destination.startswith("The hub URL"):
            assert {HUB_TOKEN} <= fqdns, "the hub's API hostname comes from the hub_api_hostname property"
            continue
        hosts = HOST.findall(destination)
        assert hosts, destination
        assert set(hosts) <= fqdns, f"{destination}: missing {set(hosts) - fqdns}"


def test_app_rules_cover_the_azure_vm_first_boot_hosts():
    hosts = _first_boot_hosts()
    assert {"github.com"} <= hosts, hosts
    assert hosts <= _https_fqdns()


def test_os_packages_are_allowed_over_http_to_ubuntu_mirrors_only():
    assert any(port == "80" for _, port in _egress_rows()), "the egress table lists OS packages on 80"
    http = [rule for rule in _app_rules() if rule["protocols"] == [{"port": "80", "type": "Http"}]]
    assert {fqdn for rule in http for fqdn in rule["target_fqdns"]} == UBUNTU_MIRRORS


def test_fl_rule_names_the_server_by_address():
    (rule,) = _network_rules()
    assert rule["destination_addresses"] == FL_IPS_TOKEN
    assert rule["destination_ports"] == [FL_PORT_TOKEN]
    assert "destination_fqdns" not in rule, "the TRE firewall runs no DNS proxy, so FQDNs in network rules fail"
    assert _schema()["properties"]["fl_server_ip_addresses"]["items"]["format"] == "ipv4"


def test_destinations_are_flips_own_and_fixed_once_deployed():
    # A workspace owner must not be able to point the TRE's egress allowlist anywhere else: the
    # destinations are FLIP's hubs and ports, and changing one means a TRE admin reinstalling.
    props = _schema()["properties"]
    assert props["hub_api_hostname"]["enum"] == FLIP_HUBS
    assert props["hub_api_hostname"]["default"] == "app.flip.aicentre.co.uk"
    assert props["fl_port"]["enum"] == [8002, 8003]
    assert props["fl_port"]["default"] == 8002
    assert (props["fl_server_ip_addresses"]["minItems"], props["fl_server_ip_addresses"]["maxItems"]) == (1, 4)
    for name in ("hub_api_hostname", "fl_server_ip_addresses", "fl_port"):
        assert props[name]["updateable"] is False, name


@pytest.mark.parametrize(
    ("address", "allowed"),
    [
        ("198.51.100.10", True),
        ("203.0.113.7", True),
        ("11.0.0.1", True),
        ("172.15.255.255", True),
        ("172.32.0.1", True),
        ("192.169.0.1", True),
        ("169.253.1.1", True),
        ("10.0.0.1", False),
        ("172.16.0.1", False),
        ("172.31.255.255", False),
        ("192.168.0.1", False),
        ("127.0.0.1", False),
        ("169.254.169.254", False),
        ("0.0.0.0", False),
        ("256.1.1.1", False),
        ("1.2.3", False),
        ("fl.example.org", False),
    ],
)
def test_fl_server_addresses_must_be_public_ipv4(address, allowed):
    pattern = _schema()["properties"]["fl_server_ip_addresses"]["items"]["pattern"]
    assert bool(re.fullmatch(pattern, address)) is allowed


def test_only_the_node_may_read_the_kit_drop():
    text = "\n".join(p.read_text() for p in TERRAFORM_DIR.glob("*.tf"))
    assignments = re.findall(r'^resource "azurerm_role_assignment" "\w+" \{\n(.*?)^\}', text, re.MULTILINE | re.DOTALL)
    assert len(assignments) == 1, "the template grants exactly one data role"
    (body,) = assignments
    assert re.search(r'^\s+role_definition_name\s+= "Storage Blob Data Reader"$', body, re.MULTILINE)
    assert re.search(
        r"^\s+principal_id\s+= azurerm_linux_virtual_machine\.node\.identity\[0\]\.principal_id$", body, re.MULTILINE
    )
    # Never the workspace's own storage account: AzureTRE's Guacamole VMs carry its key.
    assert not re.search(r'data "azurerm_storage_account"', text)


def test_cloud_init_writes_the_settings_flip_node_reads():
    def node_env_keys(path: Path) -> set[str]:
        return set(re.findall(r"^\s+([A-Z_]+)=\$\{", path.read_text(), re.MULTILINE))

    ours = node_env_keys(TERRAFORM_DIR / "templates" / "cloud-init.yaml.tftpl")
    vm = node_env_keys(REPO_ROOT / "trust/deploy/terraform/azure/vm/templates/cloud-init.yaml.tftpl")
    assert ours == vm


def test_cloud_init_installs_flip_node_from_the_release():
    text = (TERRAFORM_DIR / "templates" / "cloud-init.yaml.tftpl").read_text()
    shipped = "trust/deploy/terraform/azure/vm/templates/flip-node.sh"
    assert f"$src/{shipped}" in text
    assert (REPO_ROOT / shipped).is_file(), "cloud-init installs the flip-node FLIP ships at this path"
    assert '--branch "$FLIP_REF"' in text


def test_no_auto_approve_anywhere():
    flag = "auto" + "-approve"
    for path in _tracked_files():
        assert flag not in path.read_text(errors="ignore"), f"{path} must not skip a confirmation"


def test_no_real_guids_or_account_ids():
    nil = "00000000-0000-0000-0000-000000000000"
    guid = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    account_id = re.compile(r"(?<![0-9-])[0-9]{12}(?![0-9])")
    for path in _tracked_files():
        text = path.read_text(errors="ignore")
        for found in guid.findall(text):
            assert found == nil, f"{path} contains a GUID: {found}"
        assert not account_id.search(text.replace(nil, "")), f"{path} contains a 12-digit account ID"


def test_every_file_carries_the_licence_header():
    for path in _tracked_files():
        if path.suffix == ".json":
            continue  # JSON has no comments
        head = "\n".join(path.read_text().splitlines()[:3])
        assert "Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust" in head, path
