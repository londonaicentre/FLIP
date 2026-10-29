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

variables {
  name_prefix         = "flipaz"
  location            = "uksouth"
  resource_group_name = "flipaz-node-rg"
}

run "default_creates_network_with_no_inbound_rules" {
  command = plan

  assert {
    condition     = length(azurerm_virtual_network.this) == 1 && length(azurerm_subnet.this) == 1
    error_message = "The default creates a VNet and a subnet."
  }
  assert {
    condition     = length(azurerm_network_security_group.this[0].security_rule) == 0
    error_message = "The NSG must have no rules: Azure's defaults deny inbound from the internet."
  }
}

run "created_network_leaves_through_its_own_nat_gateway" {
  command = plan

  assert {
    condition     = length(output.nat_gateway_ids) == 1 && length(azurerm_subnet_nat_gateway_association.this) == 1
    error_message = "A created network gets a NAT gateway on the node subnet."
  }
  assert {
    condition     = length(azurerm_public_ip.nat) == 1 && azurerm_public_ip.nat[0].sku == "Standard"
    error_message = "The NAT gateway's IP is created with the network, in this module, not taken from bootstrap."
  }
  assert {
    condition     = azurerm_subnet.this[0].default_outbound_access_enabled == false
    error_message = "The subnet must not fall back to Azure's implicit outbound access."
  }
}

run "existing_subnet_creates_nothing" {
  command = plan

  variables {
    existing_subnet_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/trust-rg/providers/Microsoft.Network/virtualNetworks/trust-vnet/subnets/flip"
  }

  assert {
    condition     = length(azurerm_virtual_network.this) == 0 && length(azurerm_network_security_group.this) == 0
    error_message = "With existing_subnet_id the module creates no network resources."
  }
  assert {
    condition     = output.subnet_id == var.existing_subnet_id
    error_message = "The existing subnet is passed through."
  }
  assert {
    condition     = length(azurerm_public_ip.nat) == 0 && length(output.nat_gateway_ids) == 0 && output.nat_public_ip_address == ""
    error_message = "On a site subnet the module creates no public IP and no NAT gateway: the site's egress applies."
  }
}
