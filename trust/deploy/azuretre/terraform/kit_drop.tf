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

# The node's kit drop, in a storage account of its own. A kit holds the trust's API key, the AES key
# and the FL client's private key, so it never goes in the workspace storage account, whose key
# AzureTRE's Guacamole VMs carry. No shared keys and no public endpoint: only Entra identities
# with a data role reach it, through the private endpoint below. flip-node downloads with the VM's
# identity, as on FLIP's own Azure VM (trust/deploy/terraform/azure/modules/kit_drop).
resource "azurerm_storage_account" "kits" {
  name                              = local.kit_storage_name
  location                          = data.azurerm_resource_group.ws.location
  resource_group_name               = data.azurerm_resource_group.ws.name
  account_tier                      = "Standard"
  account_replication_type          = "LRS" # a lost kit is sent again, never restored
  account_kind                      = "StorageV2"
  min_tls_version                   = "TLS1_2"
  https_traffic_only_enabled        = true
  shared_access_key_enabled         = false
  public_network_access_enabled     = false
  allow_nested_items_to_be_public   = false
  default_to_oauth_authentication   = true
  cross_tenant_replication_enabled  = false
  infrastructure_encryption_enabled = true
  tags                              = local.tre_workspace_service_tags

  network_rules {
    default_action = "Deny"
    bypass         = ["None"]
  }

  lifecycle { ignore_changes = [tags] }
}

# Through Resource Manager (storage_account_id): no data-plane access needed to create it.
resource "azurerm_storage_container" "kits" {
  name                  = "kits"
  storage_account_id    = azurerm_storage_account.kits.id
  container_access_type = "private"
}

# Kits are deleted a day after upload (Azure runs the rule daily, so within about 48 hours).
resource "azurerm_storage_management_policy" "kits" {
  storage_account_id = azurerm_storage_account.kits.id

  rule {
    name    = "expire-kits"
    enabled = true
    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["${azurerm_storage_container.kits.name}/"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = 1
      }
    }
  }
}

# The only data role the template grants. A TRE admin grants themselves Storage Blob Data
# Contributor on the container to upload (README.md).
resource "azurerm_role_assignment" "node_reads_kits" {
  scope                = azurerm_storage_container.kits.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_linux_virtual_machine.node.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_private_endpoint" "kits" {
  name                = "pe-${local.kit_storage_name}"
  location            = data.azurerm_resource_group.ws.location
  resource_group_name = data.azurerm_resource_group.ws.name
  subnet_id           = azurerm_subnet.node.id
  tags                = local.tre_workspace_service_tags

  private_dns_zone_group {
    name                 = "private-dns-zone-group"
    private_dns_zone_ids = [data.azurerm_private_dns_zone.blobcore.id]
  }

  private_service_connection {
    name                           = "psc-blob-${local.kit_storage_name}"
    private_connection_resource_id = azurerm_storage_account.kits.id
    is_manual_connection           = false
    subresource_names              = ["blob"]
  }

  lifecycle { ignore_changes = [tags] }

  # One operation on the VNet at a time, as AzureTRE's base workspace does.
  depends_on = [azurerm_subnet_network_security_group_association.node]
}
