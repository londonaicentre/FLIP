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

# The kit drop hands a trust node its kit (kit file + FL participant kit) without a shell or a
# secret in any command: the operator uploads with their Entra identity, the node downloads with
# its managed identity, and the blob is gone a day later. Entra-only (no shared keys), so no key
# can leak through Terraform state or a log.

locals {
  tags = {
    project = "flip"
    purpose = "azure-trust-node"
  }
}

resource "azurerm_storage_account" "this" {
  # checkov:skip=CKV2_AZURE_1:Platform-managed encryption at rest; a customer-managed key needs Key Vault, which a kit drop holding one-day blobs does not justify
  # checkov:skip=CKV_AZURE_206:Locally redundant on purpose: a lost kit is re-uploaded, never restored
  # checkov:skip=CKV2_AZURE_33:No private endpoint: the firewall admits only the node subnet's service endpoint and the operator's IP
  # checkov:skip=CKV2_AZURE_38:No soft delete: kits carry secrets and must be gone when they expire
  # checkov:skip=CKV_AZURE_59:Public network access stays on so the operator can upload; the firewall defaults to Deny
  # checkov:skip=CKV_AZURE_33:No queue service is used
  # checkov:skip=CKV_AZURE_36:No trusted-service bypass: nothing but the node and the operator has reason to read a kit
  name                            = var.storage_account_name
  location                        = var.location
  resource_group_name             = var.resource_group_name
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  default_to_oauth_authentication = true
  tags                            = local.tags

  network_rules {
    default_action             = "Deny"
    bypass                     = ["None"]
    virtual_network_subnet_ids = var.allowed_subnet_ids
    ip_rules                   = var.allowed_ips
  }
}

resource "azurerm_storage_container" "kits" {
  # checkov:skip=CKV2_AZURE_21:Blob read logging needs a Log Analytics workspace, a standing cost the trial does not carry
  name                  = "kits"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "this" {
  storage_account_id = azurerm_storage_account.this.id

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

resource "azurerm_role_assignment" "reader" {
  scope                = azurerm_storage_container.kits.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = var.reader_principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "uploader" {
  scope                = azurerm_storage_container.kits.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.uploader_principal_id
}
