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

# The node's kit drop: a private container in the workspace storage account, which only the
# workspace network reaches. flip-node downloads kits from it with the VM's identity, as on FLIP's
# own Azure VM. Created through Resource Manager (storage_account_id), so the account's firewall
# does not stand in the way.
resource "azurerm_storage_container" "kits" {
  name                  = "flip-kits-${local.short_service_id}"
  storage_account_id    = data.azurerm_storage_account.ws.id
  container_access_type = "private"
}

resource "azurerm_role_assignment" "node_reads_kits" {
  scope                = azurerm_storage_container.kits.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_linux_virtual_machine.node.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}
