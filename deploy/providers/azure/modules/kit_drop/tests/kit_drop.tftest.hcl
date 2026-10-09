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
  name_prefix           = "flipaz"
  location              = "uksouth"
  resource_group_name   = "flipaz-node-rg"
  storage_account_name  = "flipazkit0123abcd"
  allowed_subnet_ids    = ["/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/flipaz-node-rg/providers/Microsoft.Network/virtualNetworks/flipaz-vnet/subnets/flipaz-node"]
  allowed_ips           = ["203.0.113.7"]
  reader_principal_id   = "00000000-0000-0000-0000-000000000000"
  uploader_principal_id = "00000000-0000-0000-0000-000000000000"
}

run "account_admits_only_entra_and_tls12" {
  command = plan

  assert {
    condition     = azurerm_storage_account.this.shared_access_key_enabled == false
    error_message = "Shared keys must be off: access is Entra-only, so no key can leak through state or a log."
  }
  assert {
    condition     = azurerm_storage_account.this.allow_nested_items_to_be_public == false
    error_message = "No container may be made public."
  }
  assert {
    condition     = azurerm_storage_account.this.min_tls_version == "TLS1_2"
    error_message = "TLS 1.2 minimum."
  }
  assert {
    condition     = azurerm_storage_container.kits.container_access_type == "private"
    error_message = "The kit container is private."
  }
}

run "firewall_denies_all_but_the_node_subnet_and_the_operator" {
  command = plan

  assert {
    condition     = azurerm_storage_account.this.network_rules[0].default_action == "Deny"
    error_message = "The storage firewall must default to Deny."
  }
  assert {
    condition     = toset(azurerm_storage_account.this.network_rules[0].virtual_network_subnet_ids) == toset(var.allowed_subnet_ids)
    error_message = "Only the node subnet is allowed in."
  }
  assert {
    condition     = toset(azurerm_storage_account.this.network_rules[0].ip_rules) == toset(["203.0.113.7"])
    error_message = "Only the operator's IP is allowed in from the internet."
  }
}

run "kits_expire_after_one_day" {
  command = plan

  assert {
    condition     = azurerm_storage_management_policy.this.rule[0].actions[0].base_blob[0].delete_after_days_since_modification_greater_than == 1
    error_message = "Kits carry secrets: blobs are deleted a day after upload."
  }
}

run "node_reads_and_operator_writes_the_container_only" {
  command = plan

  assert {
    condition     = azurerm_role_assignment.reader.role_definition_name == "Storage Blob Data Reader" && azurerm_role_assignment.reader.principal_id == var.reader_principal_id
    error_message = "The node identity gets Storage Blob Data Reader."
  }
  assert {
    condition     = azurerm_role_assignment.uploader.role_definition_name == "Storage Blob Data Contributor" && azurerm_role_assignment.uploader.principal_id == var.uploader_principal_id
    error_message = "The operator gets Storage Blob Data Contributor to upload."
  }
}
