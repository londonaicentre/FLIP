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

# Shown on the workspace service in the TRE portal.

output "vm_name" {
  value = azurerm_linux_virtual_machine.node.name
}

output "kit_drop_url" {
  description = "The container the node fetches its kit from"
  value       = local.kit_drop_url
}

output "kit_upload_command" {
  description = "For a TRE admin on the TRE network (the Admin VM), holding Storage Blob Data Contributor on the container"
  value       = "az storage blob upload --auth-mode login --blob-url ${local.kit_drop_url}/<kit>.tar.gz --file <kit>.tar.gz"
}

output "status_command" {
  value = "${local.run_command} 'flip-node status'"
}

output "join_command" {
  description = "Installs the uploaded kit and brings the trust up; HUB is stag or prod"
  value       = "${local.run_command} 'flip-node join <kit>.tar.gz <KIT> <HUB>'"
}
