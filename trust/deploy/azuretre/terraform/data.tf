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

data "azurerm_client_config" "current" {
  provider = azurerm.core
}

data "azurerm_resource_group" "ws" {
  name = "rg-${local.workspace_resource_name_suffix}"
}

data "azurerm_virtual_network" "ws" {
  name                = "vnet-${local.workspace_resource_name_suffix}"
  resource_group_name = data.azurerm_resource_group.ws.name
}

# The base workspace's subnet: the node's subnet takes its route table.
data "azurerm_subnet" "services" {
  name                 = "ServicesSubnet"
  virtual_network_name = data.azurerm_virtual_network.ws.name
  resource_group_name  = data.azurerm_resource_group.ws.name
}

# The core TRE's blob zone, linked to every workspace VNet, as the base workspace looks it up.
data "azurerm_private_dns_zone" "blobcore" {
  provider            = azurerm.core
  name                = "privatelink.blob.core.windows.net"
  resource_group_name = "rg-${var.tre_id}"
}
