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

# The node's own subnet, from the address space the TRE API allocated; the pipeline's first step
# adds that space to the workspace VNet.
resource "azurerm_subnet" "node" {
  name                            = "FlipNodeSubnet${local.short_service_id}"
  resource_group_name             = data.azurerm_resource_group.ws.name
  virtual_network_name            = data.azurerm_virtual_network.ws.name
  address_prefixes                = [var.address_space]
  default_outbound_access_enabled = false
}

# The workspace's route table, whichever subscription layout the TRE uses: everything leaves
# through the TRE firewall.
resource "azurerm_subnet_route_table_association" "node" {
  subnet_id      = azurerm_subnet.node.id
  route_table_id = data.azurerm_subnet.services.route_table_id
}

# Nothing inbound. Outbound only what the node uses; the TRE firewall narrows 443 and 80 to the
# hosts in template_schema.json's pipeline.
resource "azurerm_network_security_group" "node" {
  name                = "nsg-flip-${local.service_resource_name_suffix}"
  location            = data.azurerm_resource_group.ws.location
  resource_group_name = data.azurerm_resource_group.ws.name
  tags                = local.tre_workspace_service_tags

  lifecycle { ignore_changes = [tags] }

  security_rule {
    name                         = "to-workspace-private-endpoints"
    description                  = "The kit drop, through the workspace storage account's private endpoint"
    priority                     = 100
    direction                    = "Outbound"
    access                       = "Allow"
    protocol                     = "Tcp"
    source_port_range            = "*"
    destination_port_range       = "443"
    source_address_prefix        = "*"
    destination_address_prefixes = data.azurerm_subnet.services.address_prefixes
  }

  security_rule {
    name                         = "to-fl-server"
    description                  = "Federated learning, to the FL server's addresses only"
    priority                     = 110
    direction                    = "Outbound"
    access                       = "Allow"
    protocol                     = "Tcp"
    source_port_range            = "*"
    destination_port_range       = tostring(var.fl_port)
    source_address_prefix        = "*"
    destination_address_prefixes = local.fl_server_ips
  }

  security_rule {
    name                       = "to-internet-https"
    priority                   = 120
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "*"
    destination_address_prefix = "Internet"
  }

  security_rule {
    name                       = "to-internet-http"
    description                = "OS packages (and certificate revocation lists, as the workspace NSG allows)"
    priority                   = 130
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "80"
    source_address_prefix      = "*"
    destination_address_prefix = "Internet"
  }

  security_rule {
    name                       = "deny-outbound-override"
    priority                   = 4096
    direction                  = "Outbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # Above Azure's default AllowVnetInBound: nothing in the VNet reaches the node either.
  security_rule {
    name                       = "deny-inbound-override"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "node" {
  subnet_id                 = azurerm_subnet.node.id
  network_security_group_id = azurerm_network_security_group.node.id

  # One operation on the VNet at a time, as AzureTRE's base workspace does.
  depends_on = [azurerm_subnet_route_table_association.node]
}
