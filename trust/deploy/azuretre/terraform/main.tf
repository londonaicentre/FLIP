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

# Azure needs an SSH key for a Linux VM. Nothing can reach SSH (the subnet NSG denies all inbound),
# and the private half stays in the TRE's Terraform state.
resource "tls_private_key" "admin" {
  algorithm = "ED25519"
}

resource "azurerm_network_interface" "node" {
  name                = "nic-flip-${local.service_resource_name_suffix}"
  location            = data.azurerm_resource_group.ws.location
  resource_group_name = data.azurerm_resource_group.ws.name
  tags                = local.tre_workspace_service_tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.node.id
    private_ip_address_allocation = "Dynamic"
  }

  lifecycle { ignore_changes = [tags] }

  # First boot clones FLIP: the subnet must route through the firewall before the VM starts.
  depends_on = [
    azurerm_subnet_route_table_association.node,
    azurerm_subnet_network_security_group_association.node,
  ]
}

resource "azurerm_linux_virtual_machine" "node" {
  name                            = local.vm_name
  location                        = data.azurerm_resource_group.ws.location
  resource_group_name             = data.azurerm_resource_group.ws.name
  size                            = local.vm_sizes[var.vm_size]
  admin_username                  = local.admin_username
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.node.id]
  encryption_at_host_enabled      = true
  secure_boot_enabled             = true
  vtpm_enabled                    = true
  custom_data                     = base64encode(local.cloud_init)
  tags                            = local.tre_workspace_service_tags

  admin_ssh_key {
    username   = local.admin_username
    public_key = tls_private_key.admin.public_key_openssh
  }

  os_disk {
    name                 = "osdisk-${local.vm_name}"
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = local.os_disk_size_gb
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  identity {
    type = "SystemAssigned"
  }

  boot_diagnostics {}

  # The release matters at first boot only; later code moves with `flip-node reprovision vX.Y.Z`,
  # which keeps the VM and its data.
  lifecycle { ignore_changes = [tags, custom_data] }
}

# /opt/flip: flip-node waits for it at LUN 0 and never provisions onto the OS disk.
resource "azurerm_managed_disk" "data" {
  name                          = "datadisk-${local.vm_name}"
  location                      = data.azurerm_resource_group.ws.location
  resource_group_name           = data.azurerm_resource_group.ws.name
  storage_account_type          = "StandardSSD_LRS"
  create_option                 = "Empty"
  disk_size_gb                  = local.data_disk_size_gb
  public_network_access_enabled = false
  network_access_policy         = "DenyAll"
  tags                          = local.tre_workspace_service_tags

  lifecycle { ignore_changes = [tags] }
}

resource "azurerm_virtual_machine_data_disk_attachment" "data" {
  managed_disk_id    = azurerm_managed_disk.data.id
  virtual_machine_id = azurerm_linux_virtual_machine.node.id
  lun                = 0
  caching            = "None"
}

resource "azurerm_dev_test_global_vm_shutdown_schedule" "node" {
  count                 = var.enable_shutdown_schedule ? 1 : 0
  virtual_machine_id    = azurerm_linux_virtual_machine.node.id
  location              = data.azurerm_resource_group.ws.location
  enabled               = true
  daily_recurrence_time = var.shutdown_time
  timezone              = var.shutdown_timezone
  tags                  = local.tre_workspace_service_tags

  notification_settings {
    enabled = false
  }

  lifecycle { ignore_changes = [tags] }
}
