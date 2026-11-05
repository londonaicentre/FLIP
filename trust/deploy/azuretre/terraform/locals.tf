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
  # AzureTRE's naming: the workspace's resources are found by these, as its own templates do.
  short_service_id               = substr(var.tre_resource_id, -4, -1)
  short_workspace_id             = substr(var.workspace_id, -4, -1)
  workspace_resource_name_suffix = "${var.tre_id}-ws-${local.short_workspace_id}"
  service_resource_name_suffix   = "${var.tre_id}-ws-${local.short_workspace_id}-svc-${local.short_service_id}"
  storage_name                   = lower(replace("stg${substr(local.workspace_resource_name_suffix, -8, -1)}", "-", ""))
  vm_name                        = "flipnode${local.short_service_id}"

  vm_sizes      = yamldecode(file("${path.module}/../porter.yaml"))["custom"]["vm_sizes"]
  fl_server_ips = jsondecode(base64decode(var.fl_server_ip_addresses))
  kit_drop_url  = "${trimsuffix(data.azurerm_storage_account.ws.primary_blob_endpoint, "/")}/${azurerm_storage_container.kits.name}"

  # FLIP's Azure VM choices (trust/deploy/terraform/azure/vm).
  admin_username    = "azureuser"
  os_disk_size_gb   = 64
  data_disk_size_gb = 128

  # Holds no secrets: settings for flip-node, which cloud-init installs from the release itself.
  cloud_init = templatefile("${path.module}/templates/cloud-init.yaml.tftpl", {
    repo_url     = "https://github.com/londonaicentre/FLIP.git"
    ref          = var.flip_release
    admin_user   = local.admin_username
    fl_backend   = var.fl_backend
    kit_drop_url = local.kit_drop_url
  })

  # flip-node runs over Run Command: the node takes no inbound connection.
  run_command = "az vm run-command invoke --ids ${azurerm_linux_virtual_machine.node.id} --command-id RunShellScript --query 'value[0].message' -o tsv --scripts"

  tre_workspace_service_tags = {
    tre_id                   = var.tre_id
    tre_workspace_id         = var.workspace_id
    tre_workspace_service_id = var.tre_resource_id
  }
}
