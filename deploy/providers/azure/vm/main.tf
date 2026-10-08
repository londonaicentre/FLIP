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
  tags = {
    project = "flip"
    purpose = "azure-trust-node"
  }
  # Rendered once, used as the VM's custom_data and exposed as an output for the tests
  # (custom_data itself is sensitive in the provider). Holds no secrets.
  cloud_init = templatefile("${path.module}/templates/cloud-init.yaml.tftpl", {
    repo_url         = var.flip_repo_url
    ref              = var.flip_ref
    admin_user       = var.admin_username
    fl_backend       = var.fl_backend
    flip_node_script = file("${path.module}/templates/flip-node.sh")
  })
}

resource "azurerm_resource_group" "node" {
  name     = "${var.name_prefix}-node-rg"
  location = var.location
  tags     = local.tags
}

module "network" {
  source              = "../modules/network"
  name_prefix         = var.name_prefix
  location            = var.location
  resource_group_name = azurerm_resource_group.node.name
  existing_subnet_id  = var.existing_subnet_id
  egress_mode         = var.egress_mode
  egress_public_ip_id = var.egress_public_ip_id
}

resource "azurerm_network_interface" "node" {
  name                = "${var.name_prefix}-node-nic"
  location            = var.location
  resource_group_name = azurerm_resource_group.node.name
  tags                = local.tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = module.network.subnet_id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = module.network.attach_public_ip_to_nic ? var.egress_public_ip_id : null
  }
}

# The NIC's own NSG, rule-less on purpose and present in every mode. Azure lets inbound
# traffic through only if both the subnet's NSG and this one allow it, so even a site subnet
# (existing_subnet_id) with a permissive NSG cannot expose the node. Azure's default rules
# deny inbound from the internet.
resource "azurerm_network_security_group" "nic" {
  name                = "${var.name_prefix}-node-nic-nsg"
  location            = var.location
  resource_group_name = azurerm_resource_group.node.name
  security_rule       = []
  tags                = local.tags
}

resource "azurerm_network_interface_security_group_association" "nic" {
  network_interface_id      = azurerm_network_interface.node.id
  network_security_group_id = azurerm_network_security_group.nic.id
}

resource "azurerm_linux_virtual_machine" "node" {
  # checkov:skip=CKV_AZURE_50:Run Command, the node's only way in, needs the guest agent's extension handling; no extensions are installed
  name                            = "${var.name_prefix}-node"
  location                        = var.location
  resource_group_name             = azurerm_resource_group.node.name
  size                            = var.vm_size
  admin_username                  = var.admin_username
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.node.id]
  secure_boot_enabled             = true
  vtpm_enabled                    = true
  custom_data                     = base64encode(local.cloud_init)
  tags                            = local.tags

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.admin_ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = var.os_disk_size_gb
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

  # The ref matters at first boot only; later code moves with `make reprovision REF=...`,
  # which keeps the VM and its data. Without this a new commit would replace the VM.
  lifecycle {
    ignore_changes = [custom_data]
  }
}

resource "azurerm_managed_disk" "data" {
  # checkov:skip=CKV_AZURE_93:Platform-managed encryption at rest; a customer-managed key needs Key Vault, which a trial node does not justify
  name                          = "${var.name_prefix}-node-data"
  location                      = var.location
  resource_group_name           = azurerm_resource_group.node.name
  storage_account_type          = "StandardSSD_LRS"
  create_option                 = "Empty"
  disk_size_gb                  = var.data_disk_size_gb
  public_network_access_enabled = false
  network_access_policy         = "DenyAll"
  tags                          = local.tags
}

resource "azurerm_virtual_machine_data_disk_attachment" "data" {
  managed_disk_id    = azurerm_managed_disk.data.id
  virtual_machine_id = azurerm_linux_virtual_machine.node.id
  lun                = 0
  caching            = "None"
}

resource "azurerm_dev_test_global_vm_shutdown_schedule" "node" {
  count                 = var.auto_shutdown_enabled ? 1 : 0
  virtual_machine_id    = azurerm_linux_virtual_machine.node.id
  location              = var.location
  enabled               = true
  daily_recurrence_time = var.auto_shutdown_time
  timezone              = var.auto_shutdown_timezone
  tags                  = local.tags

  notification_settings {
    enabled = false
  }
}
