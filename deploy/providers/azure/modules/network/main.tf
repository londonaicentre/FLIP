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

locals {
  create = var.existing_subnet_id == ""
  tags = {
    project = "flip"
    purpose = "azure-trust-node"
  }
}

resource "azurerm_virtual_network" "this" {
  count               = local.create ? 1 : 0
  name                = "${var.name_prefix}-vnet"
  location            = var.location
  resource_group_name = var.resource_group_name
  address_space       = var.address_space
  tags                = local.tags
}

resource "azurerm_subnet" "this" {
  count                           = local.create ? 1 : 0
  name                            = "${var.name_prefix}-node"
  resource_group_name             = var.resource_group_name
  virtual_network_name            = azurerm_virtual_network.this[0].name
  address_prefixes                = [var.subnet_prefix]
  service_endpoints               = ["Microsoft.Storage"]
  default_outbound_access_enabled = false
}

# No rules on purpose: Azure's default rules already deny inbound traffic from the internet,
# and nothing reaches the node except through Run Command.
resource "azurerm_network_security_group" "this" {
  count               = local.create ? 1 : 0
  name                = "${var.name_prefix}-node-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name
  security_rule       = []
  tags                = local.tags
}

resource "azurerm_subnet_network_security_group_association" "this" {
  count                     = local.create ? 1 : 0
  subnet_id                 = azurerm_subnet.this[0].id
  network_security_group_id = azurerm_network_security_group.this[0].id
}

resource "azurerm_nat_gateway" "this" {
  count               = local.create && var.egress_mode == "nat" ? 1 : 0
  name                = "${var.name_prefix}-nat"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku_name            = "Standard"
  tags                = local.tags
}

resource "azurerm_nat_gateway_public_ip_association" "this" {
  count                = length(azurerm_nat_gateway.this)
  nat_gateway_id       = azurerm_nat_gateway.this[0].id
  public_ip_address_id = var.egress_public_ip_id
}

resource "azurerm_subnet_nat_gateway_association" "this" {
  count          = length(azurerm_nat_gateway.this)
  subnet_id      = azurerm_subnet.this[0].id
  nat_gateway_id = azurerm_nat_gateway.this[0].id
}
