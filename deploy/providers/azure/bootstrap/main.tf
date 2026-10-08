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

# Long-lived pieces that outlive any node: Terraform state storage, the budget alert and
# the static outbound IP. Bootstrap keeps its own state locally (gitignored); the vm root
# keeps its state in the storage account made here.

locals {
  tags = {
    project = "flip"
    purpose = "azure-trust-node"
  }
}

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "bootstrap" {
  name     = "${var.name_prefix}-bootstrap-rg"
  location = var.location
  tags     = local.tags
}

# tflint-ignore: azurerm_resources_missing_prevent_destroy # make destroy-bootstrap must be able to remove it on a trial; blob versioning protects the state
resource "azurerm_storage_account" "state" {
  # checkov:skip=CKV2_AZURE_1:Platform-managed encryption; a customer-managed key needs Key Vault, which a trial node does not justify
  # checkov:skip=CKV2_AZURE_33:No private endpoint: the operator reaches state from their own machine, Entra ID only, shared keys off
  # checkov:skip=CKV_AZURE_59:Public endpoint kept for the operator's machine; anonymous access and shared keys are both off
  # checkov:skip=CKV_AZURE_206:LRS on purpose: versioned, recreatable Terraform state on a trial
  # checkov:skip=CKV_AZURE_33:No queues are used
  name                            = var.state_storage_account_name
  resource_group_name             = azurerm_resource_group.bootstrap.name
  location                        = azurerm_resource_group.bootstrap.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  tags                            = local.tags

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 7
    }

    container_delete_retention_policy {
      days = 7
    }
  }
}

# tflint-ignore: azurerm_resources_missing_prevent_destroy # make destroy-bootstrap must be able to remove it on a trial; blob versioning protects the state
resource "azurerm_storage_container" "state" {
  # checkov:skip=CKV2_AZURE_21:Blob read logging needs a paid Log Analytics workspace; state access is Entra ID only
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
}

# The operator running bootstrap reads and writes state through Entra ID (shared keys are off).
resource "azurerm_role_assignment" "state_blob" {
  scope                = azurerm_storage_account.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

# The node's one outbound address. Lives here so destroying and recreating the node keeps it,
# and so it can move between the VM's NIC (egress_mode=public_ip) and a NAT gateway (nat).
resource "azurerm_public_ip" "egress" {
  name                = "${var.name_prefix}-egress-ip"
  resource_group_name = azurerm_resource_group.bootstrap.name
  location            = azurerm_resource_group.bootstrap.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
}

resource "azurerm_consumption_budget_subscription" "trial" {
  count           = var.create_budget ? 1 : 0
  name            = "${var.name_prefix}-budget"
  subscription_id = "/subscriptions/${var.subscription_id}"
  amount          = var.budget_amount
  time_grain      = "Monthly"

  time_period {
    start_date = var.budget_start_date
  }

  dynamic "notification" {
    for_each = [20, 50, 80]
    content {
      enabled        = true
      threshold      = notification.value
      operator       = "GreaterThanOrEqualTo"
      threshold_type = "Actual"
      contact_emails = var.alert_emails
    }
  }

  lifecycle {
    ignore_changes = [time_period]
  }
}
