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

mock_provider "azurerm" {}

# A real, throwaway public key (its private half was deleted): the provider validates key data even when mocked.
variables {
  subscription_id      = "00000000-0000-0000-0000-000000000000"
  admin_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAT8RdfFQymZ+0z9XcvL1GYWdmlY8Ci6MB9I5PXKhOKU flip-azure-terraform-test-only"
  flip_ref             = "0123456789abcdef0123456789abcdef01234567" # pragma: allowlist secret
  operator_ip          = "203.0.113.7"
}

run "vm_is_hardened" {
  command = plan

  assert {
    condition     = azurerm_linux_virtual_machine.node.disable_password_authentication == true
    error_message = "Password login must be off."
  }
  assert {
    condition     = azurerm_linux_virtual_machine.node.secure_boot_enabled && azurerm_linux_virtual_machine.node.vtpm_enabled
    error_message = "Trusted Launch (secure boot + vTPM) must be on."
  }
  assert {
    condition     = azurerm_linux_virtual_machine.node.size == "Standard_D4s_v5"
    error_message = "Default size is Standard_D4s_v5 (fits the 4-vCPU trial quota)."
  }
  assert {
    condition     = azurerm_linux_virtual_machine.node.source_image_reference[0].version == "latest"
    error_message = "The image follows Canonical's current 24.04 image unless image_version pins one."
  }
}

run "nic_has_no_public_ip" {
  command = plan

  assert {
    condition     = azurerm_network_interface.node.ip_configuration[0].public_ip_address_id == null
    error_message = "The node's NIC never carries a public IP: outbound goes through the NAT gateway or the site's egress."
  }
}

run "cloud_init_pins_the_ref_and_installs_flip_node" {
  command = plan

  assert {
    condition     = strcontains(output.cloud_init, "FLIP_REF=0123456789abcdef0123456789abcdef01234567")
    error_message = "cloud-init must carry the pinned ref."
  }
  assert {
    condition     = strcontains(output.cloud_init, "/usr/local/sbin/flip-node")
    error_message = "cloud-init must install flip-node."
  }
  assert {
    condition     = can(yamldecode(output.cloud_init))
    error_message = "The rendered cloud-init must be valid YAML, or first boot does nothing."
  }
  assert {
    condition     = one([for f in yamldecode(output.cloud_init).write_files : f.content if f.path == "/usr/local/sbin/flip-node"]) == file("${path.module}/templates/flip-node.sh")
    error_message = "The flip-node embedded in cloud-init must be byte-identical to templates/flip-node.sh."
  }
}

run "rejects_a_branch_name_as_ref" {
  command = plan

  variables {
    flip_ref = "develop"
  }

  expect_failures = [var.flip_ref]
}

run "rejects_a_prefix_no_storage_account_name_allows" {
  command = plan

  variables {
    name_prefix = "flip-az"
  }

  expect_failures = [var.name_prefix]
}

run "rejects_a_prefix_too_long_for_a_storage_account_name" {
  command = plan

  variables {
    name_prefix = "flipazuresite01"
  }

  expect_failures = [var.name_prefix]
}

run "pinned_image_version_reaches_the_vm" {
  command = plan

  variables {
    image_version = "24.04.202510010"
  }

  assert {
    condition     = azurerm_linux_virtual_machine.node.source_image_reference[0].version == "24.04.202510010"
    error_message = "A pinned image_version must reach the VM's image reference."
  }
}

run "rejects_an_image_version_that_is_not_a_version" {
  command = plan

  variables {
    image_version = "newest"
  }

  expect_failures = [var.image_version]
}

run "data_disk_is_lun_0" {
  command = plan

  assert {
    condition     = azurerm_virtual_machine_data_disk_attachment.data.lun == 0
    error_message = "flip-node looks for the data disk at LUN 0."
  }
}

run "auto_shutdown_on_by_default_and_optional" {
  command = plan

  assert {
    condition     = length(azurerm_dev_test_global_vm_shutdown_schedule.node) == 1
    error_message = "Auto-shutdown is on by default."
  }
}

run "auto_shutdown_can_be_turned_off" {
  command = plan

  variables {
    auto_shutdown_enabled = false
  }

  assert {
    condition     = length(azurerm_dev_test_global_vm_shutdown_schedule.node) == 0
    error_message = "auto_shutdown_enabled=false removes the schedule."
  }
}

# Inbound needs both the subnet's NSG and the NIC's NSG to allow it. The NIC gets its own NSG in
# every mode, denying all inbound above Azure's defaults (which allow the VNet), so a site subnet
# (existing_subnet_id) with a permissive NSG still cannot expose the node.
run "nic_nsg_denies_all_inbound" {
  command = plan

  assert {
    condition = length(azurerm_network_security_group.nic.security_rule) == 1 && alltrue([
      for r in azurerm_network_security_group.nic.security_rule :
      r.direction == "Inbound" && r.access == "Deny" && r.priority == 4096 && r.protocol == "*" &&
      r.source_address_prefix == "*" && r.destination_port_range == "*"
    ])
    error_message = "The NIC's NSG must hold exactly one rule, denying all inbound at priority 4096."
  }
}

# The association itself is checked statically (tests/test_static_guards.py): its IDs are
# unknown at plan, and a mocked apply cannot produce IDs the provider accepts.
run "nic_nsg_also_guards_a_site_subnet" {
  command = plan

  variables {
    existing_subnet_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/trust-rg/providers/Microsoft.Network/virtualNetworks/trust-vnet/subnets/flip"
  }

  assert {
    condition = azurerm_network_security_group.nic.name == "flipaz-node-nic-nsg" && alltrue([
      for r in azurerm_network_security_group.nic.security_rule : r.direction == "Inbound" && r.access == "Deny"
    ])
    error_message = "With a site subnet the NIC still carries its own deny-all-inbound NSG."
  }
}

run "data_disk_has_no_public_network_access" {
  command = plan

  assert {
    condition     = azurerm_managed_disk.data.public_network_access_enabled == false && azurerm_managed_disk.data.network_access_policy == "DenyAll"
    error_message = "The data disk must not be exportable over the public network."
  }
}

run "kit_drop_on_by_default_and_named_on_the_node" {
  command = plan

  assert {
    condition     = length(module.kit_drop) == 1
    error_message = "The kit drop is on by default."
  }
  assert {
    condition     = can(regex("KIT_DROP_URL=https://flipazkit[0-9a-f]{8}\\.blob\\.core\\.windows\\.net/kits\\n", output.cloud_init))
    error_message = "cloud-init must tell flip-node where its kit drop is."
  }
  assert {
    condition     = output.kit_drop_url == "https://${local.kit_storage_account_name}.blob.core.windows.net/kits"
    error_message = "The kit drop URL is an output for kit-upload."
  }
}

run "kit_drop_can_be_turned_off" {
  command = plan

  variables {
    kit_drop_enabled = false
  }

  assert {
    condition     = length(module.kit_drop) == 0 && strcontains(output.cloud_init, "KIT_DROP_URL=\n")
    error_message = "With the kit drop off nothing is created and flip-node is told there is none."
  }
}

run "kit_drop_needs_the_operator_ip" {
  command = plan

  variables {
    operator_ip = ""
  }

  expect_failures = [var.operator_ip]
}

run "kit_drop_refuses_an_ipv6_operator_ip" {
  command = plan

  variables {
    operator_ip = "2001:db8::1"
  }

  expect_failures = [var.operator_ip]
}
