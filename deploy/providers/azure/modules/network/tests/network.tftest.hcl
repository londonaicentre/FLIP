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
  assert {
    condition     = output.attach_public_ip_to_nic == true
    error_message = "public_ip is the default egress mode."
  }
  assert {
    condition     = length(azurerm_nat_gateway.this) == 0
    error_message = "public_ip mode creates no NAT gateway."
  }
}

run "nat_mode_uses_the_bootstrap_ip" {
  command = plan

  variables {
    egress_mode         = "nat"
    egress_public_ip_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/flipaz-bootstrap-rg/providers/Microsoft.Network/publicIPAddresses/flipaz-egress-ip"
  }

  assert {
    condition     = length(azurerm_nat_gateway.this) == 1 && output.attach_public_ip_to_nic == false
    error_message = "nat mode creates a NAT gateway and puts no public IP on the NIC."
  }
  assert {
    condition     = azurerm_nat_gateway_public_ip_association.this[0].public_ip_address_id == var.egress_public_ip_id
    error_message = "The NAT gateway must use the bootstrap IP."
  }
}

run "none_mode_creates_no_egress" {
  command = plan

  variables {
    egress_mode = "none"
  }

  assert {
    condition     = length(azurerm_nat_gateway.this) == 0 && output.attach_public_ip_to_nic == false
    error_message = "none mode creates no egress at all."
  }
}

run "existing_subnet_creates_nothing" {
  command = plan

  variables {
    existing_subnet_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/trust-rg/providers/Microsoft.Network/virtualNetworks/trust-vnet/subnets/flip"
    egress_mode        = "none"
  }

  assert {
    condition     = length(azurerm_virtual_network.this) == 0 && length(azurerm_network_security_group.this) == 0
    error_message = "With existing_subnet_id the module creates no network resources."
  }
  assert {
    condition     = output.subnet_id == var.existing_subnet_id
    error_message = "The existing subnet is passed through."
  }
}

run "nat_needs_a_created_subnet" {
  command = plan

  variables {
    existing_subnet_id  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/trust-rg/providers/Microsoft.Network/virtualNetworks/trust-vnet/subnets/flip"
    egress_mode         = "nat"
    egress_public_ip_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/flipaz-bootstrap-rg/providers/Microsoft.Network/publicIPAddresses/flipaz-egress-ip"
  }

  expect_failures = [var.egress_mode]
}

run "rejects_unknown_egress_mode" {
  command = plan

  variables {
    egress_mode = "open"
  }

  expect_failures = [var.egress_mode]
}
