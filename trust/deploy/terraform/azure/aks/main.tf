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

data "azurerm_client_config" "current" {}

locals {
  tags = {
    project = "flip"
    purpose = "azure-trust-node-aks"
  }
}

resource "azurerm_resource_group" "aks" {
  name     = "${var.name_prefix}-aks-rg"
  location = var.location
  tags     = local.tags
}

# The same network as the VM node: a site's subnet with the site's egress, or a created one that
# leaves through its own NAT gateway. The cluster creates no outbound IP of its own either way.
module "network" {
  source              = "../modules/network"
  name_prefix         = "${var.name_prefix}-aks"
  location            = var.location
  resource_group_name = azurerm_resource_group.aks.name
  existing_subnet_id  = var.existing_subnet_id
}

# The API server admits the operator and the nodes' own egress. AKS adds a load balancer's outbound
# IP by itself, but not a NAT gateway's or a site firewall's, and a node that cannot reach its API
# server never joins (the reimage fails with CSE exit 51).
locals {
  node_egress_ips  = var.existing_subnet_id != "" ? var.node_egress_ips : [module.network.nat_public_ip_address]
  node_api_ranges  = [for ip in local.node_egress_ips : "${ip}/32"]
  api_server_admit = distinct(concat(["${var.operator_ip}/32"], local.node_api_ranges))
}

resource "azurerm_kubernetes_cluster" "this" {
  # checkov:skip=CKV_AZURE_115:A private cluster needs a jump host (standing cost); the API server admits only the operator's IP instead
  # checkov:skip=CKV_AZURE_117:Platform-managed disk encryption; a disk encryption set needs Key Vault, which a trial cluster does not justify
  # checkov:skip=CKV_AZURE_170:The Free control-plane tier; the paid SLA is for production clusters
  # checkov:skip=CKV_AZURE_168:One node runs the whole trust stack, which needs more than 50 pods' headroom only on a larger pool
  # checkov:skip=CKV_AZURE_226:Ephemeral OS disks need a size whose cache fits the disk; the trial size's does not
  # checkov:skip=CKV_AZURE_227:Host encryption must be enabled on the subscription first
  # checkov:skip=CKV_AZURE_232:One system pool runs the workload on a one-node trial cluster
  # checkov:skip=CKV_AZURE_141:Local accounts are disabled (local_account_disabled); this check reads a deprecated field
  # checkov:skip=CKV_AZURE_4:Container insights needs a Log Analytics workspace, a standing cost the trial does not carry
  # checkov:skip=CKV_AZURE_172:The Key Vault secrets provider is not used; the kit arrives as a plain Secret
  # checkov:skip=CKV2_AZURE_29:Azure CNI overlay with Cilium; this check predates it
  # checkov:skip=CKV_AZURE_116:The Azure Policy add-on runs Gatekeeper pods that would compete with the trust stack on a one-node cluster
  # checkov:skip=CKV_AZURE_171:Upgrades are an operator action: on one node each surges a second node the trial quota cannot hold
  # No automatic_upgrade_channel (CKV_AZURE_171 above), and the node OS patches in place rather than reimaging.
  name                    = "${var.name_prefix}-aks"
  location                = var.location
  resource_group_name     = azurerm_resource_group.aks.name
  dns_prefix              = "${var.name_prefix}-aks"
  kubernetes_version      = var.kubernetes_version != "" ? var.kubernetes_version : null
  sku_tier                = "Free"
  local_account_disabled  = true
  node_os_upgrade_channel = "Unmanaged"
  tags                    = local.tags

  # The NAT gateway must be on the subnet before the cluster checks its outbound path.
  depends_on = [module.network]

  default_node_pool {
    name           = "system"
    node_count     = 1
    vm_size        = var.node_size
    vnet_subnet_id = module.network.subnet_id
    os_disk_type   = "Managed"
    max_pods       = 110
    upgrade_settings {
      max_surge = "10%"
    }
  }

  identity {
    type = "SystemAssigned"
  }

  azure_active_directory_role_based_access_control {
    tenant_id          = data.azurerm_client_config.current.tenant_id
    azure_rbac_enabled = true
  }

  api_server_access_profile {
    authorized_ip_ranges = local.api_server_admit
  }

  # The chart's NetworkPolicies are its isolation; Cilium enforces them.
  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_data_plane  = "cilium"
    network_policy      = "cilium"
    # A site subnet routes outbound through the site's firewall (its route table); a created
    # one through the module's NAT gateway. Neither gives the cluster a public outbound IP.
    outbound_type     = var.existing_subnet_id != "" ? "userDefinedRouting" : "userAssignedNATGateway"
    load_balancer_sku = "standard"
  }
}

# The cluster identity manages its own resources in the node subnet, and nothing broader.
resource "azurerm_role_assignment" "cluster_uses_subnet" {
  scope                = module.network.subnet_id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_kubernetes_cluster.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "operator_cluster_admin" {
  scope                = azurerm_kubernetes_cluster.this.id
  role_definition_name = "Azure Kubernetes Service RBAC Cluster Admin"
  principal_id         = data.azurerm_client_config.current.object_id
  principal_type       = "User" # applied by the operator's own az login
}
