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

# Credential-free: plans against mocked Azure, with the values the TRE API and resource processor pass.

mock_provider "azurerm" {}

mock_provider "azurerm" {
  alias = "core"
}

# The provider checks ID shapes even when mocked; the subscription is the nil GUID.
override_data {
  target = data.azurerm_private_dns_zone.blobcore
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mytre/providers/Microsoft.Network/privateDnsZones/privatelink.blob.core.windows.net"
  }
}

override_data {
  target = data.azurerm_subnet.services
  values = {
    route_table_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mytre/providers/Microsoft.Network/routeTables/rt-mytre"
  }
}

override_data {
  target = data.azurerm_resource_group.ws
  values = {
    location = "uksouth"
  }
}

variables {
  tre_id                   = "mytre"
  tre_resource_id          = "flip-test-svc-1b2c"
  workspace_id             = "flip-test-ws-0a9e"
  address_space            = "10.1.5.0/24"
  flip_release             = "v0.14.0"
  fl_backend               = "nvflare"
  vm_size                  = "4 CPU | 16GB RAM"
  fl_server_ip_addresses   = "WyIxOTguNTEuMTAwLjEwIiwgIjE5OC41MS4xMDAuMTEiXQ==" # ["198.51.100.10", "198.51.100.11"]
  fl_port                  = 8002
  enable_shutdown_schedule = false
  shutdown_time            = ""
  shutdown_timezone        = "UTC"
}

run "subnet_and_nsg" {
  command = plan

  assert {
    condition     = azurerm_subnet.node.address_prefixes == tolist(["10.1.5.0/24"])
    error_message = "The node's subnet is the address space the TRE allocated."
  }
  assert {
    condition     = length([for r in azurerm_network_security_group.node.security_rule : r if r.direction == "Inbound" && r.access == "Allow"]) == 0
    error_message = "The node's NSG allows nothing inbound."
  }
  assert {
    condition = one([
      for r in azurerm_network_security_group.node.security_rule : r.destination_address_prefixes
      if r.access == "Allow" && r.destination_port_range == "8002"
    ]) == toset(["198.51.100.10", "198.51.100.11"])
    error_message = "The FL port is open to the FL server's addresses only."
  }
  assert {
    condition = toset([
      for r in azurerm_network_security_group.node.security_rule : r.destination_port_range
      if r.direction == "Outbound" && r.access == "Allow"
    ]) == toset(["443", "80", "8002"])
    error_message = "Outbound allows only 443, 80 and the FL port."
  }
  assert {
    condition = one([
      for r in azurerm_network_security_group.node.security_rule : r.destination_address_prefix
      if r.name == "to-kit-drop"
    ]) == "10.1.5.0/24"
    error_message = "The kit drop's private endpoint is reached inside the node's own subnet only."
  }
}

run "kit_drop" {
  command = plan

  assert {
    condition     = azurerm_storage_account.kits.shared_access_key_enabled == false
    error_message = "Shared keys must be off: only Entra identities with a data role read a kit."
  }
  assert {
    condition     = azurerm_storage_account.kits.public_network_access_enabled == false
    error_message = "The kit drop has no public endpoint."
  }
  assert {
    condition     = azurerm_storage_account.kits.name == "stfkmytre0a9e1b2c"
    error_message = "The kit drop is the service's own storage account, not the workspace's."
  }
  assert {
    condition     = azurerm_private_endpoint.kits.private_service_connection[0].subresource_names == tolist(["blob"])
    error_message = "The kit drop is reached through a blob private endpoint."
  }
  assert {
    condition     = azurerm_private_endpoint.kits.private_dns_zone_group[0].private_dns_zone_ids == tolist(["/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mytre/providers/Microsoft.Network/privateDnsZones/privatelink.blob.core.windows.net"])
    error_message = "The private endpoint registers in the core TRE's privatelink.blob zone."
  }
  assert {
    condition     = azurerm_role_assignment.node_reads_kits.role_definition_name == "Storage Blob Data Reader"
    error_message = "The node may only read kits."
  }
  assert {
    condition     = azurerm_storage_management_policy.kits.rule[0].actions[0].base_blob[0].delete_after_days_since_modification_greater_than == 1
    error_message = "Kits are deleted a day after upload."
  }
}

run "vm" {
  command = plan

  assert {
    condition     = azurerm_linux_virtual_machine.node.size == "Standard_D4s_v5"
    error_message = "vm_size maps through porter.yaml's custom.vm_sizes."
  }
  assert {
    condition     = azurerm_linux_virtual_machine.node.secure_boot_enabled && azurerm_linux_virtual_machine.node.vtpm_enabled
    error_message = "Trusted Launch (secure boot + vTPM) must be on."
  }
  assert {
    condition     = azurerm_linux_virtual_machine.node.source_image_reference[0].offer == "ubuntu-24_04-lts"
    error_message = "The node runs Ubuntu 24.04, as FLIP's Azure VM does."
  }
  assert {
    condition     = azurerm_network_interface.node.ip_configuration[0].public_ip_address_id == null
    error_message = "The node has no public IP."
  }
  assert {
    condition     = length(azurerm_dev_test_global_vm_shutdown_schedule.node) == 0
    error_message = "No daily shutdown unless asked for."
  }
}

run "cloud_init" {
  command = plan

  assert {
    condition     = can(yamldecode(nonsensitive(base64decode(azurerm_linux_virtual_machine.node.custom_data))))
    error_message = "The rendered cloud-init must be valid YAML, or first boot does nothing."
  }
  assert {
    condition = one([
      for f in yamldecode(nonsensitive(base64decode(azurerm_linux_virtual_machine.node.custom_data))).write_files : f.content
      if f.path == "/etc/flip/node.env"
    ]) == "FLIP_REPO_URL=https://github.com/londonaicentre/FLIP.git\nFLIP_REF=v0.14.0\nFLIP_ADMIN_USER=azureuser\nFL_BACKEND=nvflare\nKIT_DROP_URL=https://stfkmytre0a9e1b2c.blob.core.windows.net/kits\n"
    error_message = "node.env must pin the release and point flip-node at the kit container."
  }
  assert {
    condition     = output.kit_drop_url == "https://stfkmytre0a9e1b2c.blob.core.windows.net/kits"
    error_message = "flip-node fetches kits from the service's own storage account."
  }
}

run "rejects_a_branch_as_release" {
  command = plan

  variables {
    flip_release = "develop"
  }

  expect_failures = [var.flip_release]
}

run "rejects_an_fl_server_hostname" {
  command = plan

  variables {
    fl_server_ip_addresses = "WyJmbC5leGFtcGxlLm9yZyJd" # ["fl.example.org"]
  }

  expect_failures = [var.fl_server_ip_addresses]
}

run "rejects_a_private_fl_server_address" {
  command = plan

  variables {
    fl_server_ip_addresses = "WyIxMC4xLjIuMyJd" # ["10.1.2.3"]
  }

  expect_failures = [var.fl_server_ip_addresses]
}

run "rejects_a_private_address_beside_a_public_one" {
  command = plan

  variables {
    fl_server_ip_addresses = "WyIxOTguNTEuMTAwLjEwIiwgIjE3Mi4zMS4wLjEiXQ==" # ["198.51.100.10", "172.31.0.1"]
  }

  expect_failures = [var.fl_server_ip_addresses]
}

run "rejects_a_link_local_fl_server_address" {
  command = plan

  variables {
    fl_server_ip_addresses = "WyIxNjkuMjU0LjE2OS4yNTQiXQ==" # ["169.254.169.254"]
  }

  expect_failures = [var.fl_server_ip_addresses]
}

run "rejects_a_loopback_fl_server_address" {
  command = plan

  variables {
    fl_server_ip_addresses = "WyIxMjcuMC4wLjEiXQ==" # ["127.0.0.1"]
  }

  expect_failures = [var.fl_server_ip_addresses]
}

run "rejects_the_unspecified_address" {
  command = plan

  variables {
    fl_server_ip_addresses = "WyIwLjAuMC4wIl0=" # ["0.0.0.0"]
  }

  expect_failures = [var.fl_server_ip_addresses]
}

run "rejects_the_azure_platform_address" {
  command = plan

  variables {
    fl_server_ip_addresses = "WyIxNjguNjMuMTI5LjE2Il0=" # pragma: allowlist secret (["168.63.129.16"])
  }

  expect_failures = [var.fl_server_ip_addresses]
}

run "rejects_the_shared_cgnat_address" {
  command = plan

  variables {
    fl_server_ip_addresses = "WyIxMDAuNjQuMC4xIl0=" # pragma: allowlist secret (["100.64.0.1"])
  }

  expect_failures = [var.fl_server_ip_addresses]
}

run "rejects_the_multicast_address" {
  command = plan

  variables {
    fl_server_ip_addresses = "WyIyMjQuMC4wLjEiXQ==" # pragma: allowlist secret (["224.0.0.1"])
  }

  expect_failures = [var.fl_server_ip_addresses]
}

run "rejects_more_than_four_fl_server_addresses" {
  command = plan

  variables {
    fl_server_ip_addresses = "WyIxOTguNTEuMTAwLjEiLCAiMTk4LjUxLjEwMC4yIiwgIjE5OC41MS4xMDAuMyIsICIxOTguNTEuMTAwLjQiLCAiMTk4LjUxLjEwMC41Il0=" # pragma: allowlist secret (five documentation addresses, base64 JSON)
  }

  expect_failures = [var.fl_server_ip_addresses]
}

run "accepts_172_32_which_is_public" {
  command = plan

  variables {
    fl_server_ip_addresses = "WyIxNzIuMzIuMC4xIl0=" # ["172.32.0.1"], just past 172.16.0.0/12
  }
}

run "rejects_a_port_other_than_the_hubs" {
  command = plan

  variables {
    fl_port = 8080
  }

  expect_failures = [var.fl_port]
}
