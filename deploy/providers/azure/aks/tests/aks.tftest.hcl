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

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id = "00000000-0000-0000-0000-000000000000"
      object_id = "00000000-0000-0000-0000-000000000000"
    }
  }
}

variables {
  subscription_id = "00000000-0000-0000-0000-000000000000"
  operator_ip     = "203.0.113.7"
}

run "api_server_admits_the_operator_and_the_nat_gateway" {
  command = plan

  override_resource {
    target = module.network.azurerm_public_ip.nat[0]
    values = {
      id         = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/flipaz-aks-rg/providers/Microsoft.Network/publicIPAddresses/flipaz-aks-nat-ip"
      ip_address = "198.51.100.9"
    }
    override_during = plan
  }

  assert {
    condition     = azurerm_kubernetes_cluster.this.api_server_access_profile[0].authorized_ip_ranges == toset(["203.0.113.7/32", "198.51.100.9/32"])
    error_message = "The API server admits the operator and the nodes' NAT gateway IP, and nothing else: AKS adds no NAT gateway IP by itself."
  }
  assert {
    condition     = output.api_server_node_ranges == "198.51.100.9/32"
    error_message = "make aks-allow-ip reads the nodes' ranges from this output."
  }
}

run "api_server_admits_the_site_egress_on_a_site_subnet" {
  command = plan

  variables {
    existing_subnet_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/trust-rg/providers/Microsoft.Network/virtualNetworks/trust-vnet/subnets/flip"
    node_egress_ips    = ["198.51.100.20"]
  }

  assert {
    condition     = azurerm_kubernetes_cluster.this.api_server_access_profile[0].authorized_ip_ranges == toset(["203.0.113.7/32", "198.51.100.20/32"])
    error_message = "On a site subnet the API server admits the operator and the site's egress IPs."
  }
}

run "site_subnet_needs_its_egress_ips" {
  command = plan

  variables {
    existing_subnet_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/trust-rg/providers/Microsoft.Network/virtualNetworks/trust-vnet/subnets/flip"
  }

  expect_failures = [var.node_egress_ips]
}

run "node_egress_ips_must_be_addresses" {
  command = plan

  variables {
    existing_subnet_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/trust-rg/providers/Microsoft.Network/virtualNetworks/trust-vnet/subnets/flip"
    node_egress_ips    = ["198.51.100.0/24"]
  }

  expect_failures = [var.node_egress_ips]
}

run "users_sign_in_with_entra_only" {
  command = plan

  assert {
    condition     = azurerm_kubernetes_cluster.this.local_account_disabled == true
    error_message = "Local (certificate) accounts must be off: every user signs in with Entra ID."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.azure_active_directory_role_based_access_control[0].azure_rbac_enabled == true
    error_message = "Authorisation is Azure RBAC."
  }
  assert {
    condition     = azurerm_role_assignment.operator_cluster_admin.role_definition_name == "Azure Kubernetes Service RBAC Cluster Admin"
    error_message = "The operator administers the cluster through Azure RBAC."
  }
  assert {
    condition     = azurerm_role_assignment.operator_cluster_admin.principal_type == "User"
    error_message = "The operator's role assignment states its principal type rather than leaving Azure to look it up."
  }
}

run "upgrades_are_the_operators" {
  command = plan

  assert {
    condition     = azurerm_kubernetes_cluster.this.automatic_upgrade_channel == null
    error_message = "Azure must not upgrade the cluster unattended: on one node an upgrade surges a second node the trial quota cannot hold."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.node_os_upgrade_channel == "Unmanaged"
    error_message = "The node OS patches in place: a node-image channel reimages through the same surge."
  }
}

run "network_policies_are_enforced" {
  command = plan

  assert {
    condition     = azurerm_kubernetes_cluster.this.network_profile[0].network_policy == "cilium" && azurerm_kubernetes_cluster.this.network_profile[0].network_data_plane == "cilium"
    error_message = "The chart's NetworkPolicies must be enforced (Cilium)."
  }
}

run "created_network_egress_is_its_own_nat_gateway" {
  command = plan

  assert {
    condition     = azurerm_kubernetes_cluster.this.network_profile[0].outbound_type == "userAssignedNATGateway"
    error_message = "On a created network the cluster leaves through the module's NAT gateway, not a load-balancer IP."
  }
  assert {
    condition     = length(module.network.nat_gateway_ids) == 1
    error_message = "The created network carries the NAT gateway."
  }
}

run "site_subnet_egress_is_the_sites" {
  command = plan

  variables {
    existing_subnet_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/trust-rg/providers/Microsoft.Network/virtualNetworks/trust-vnet/subnets/flip"
    node_egress_ips    = ["198.51.100.20"]
  }

  assert {
    condition     = azurerm_kubernetes_cluster.this.network_profile[0].outbound_type == "userDefinedRouting"
    error_message = "On a site subnet the cluster follows the site's routes and creates no outbound IP."
  }
  assert {
    condition     = length(module.network.nat_gateway_ids) == 0
    error_message = "Nothing outbound is created on a site subnet."
  }
  assert {
    condition     = azurerm_role_assignment.cluster_uses_subnet.scope == var.existing_subnet_id && azurerm_role_assignment.cluster_uses_subnet.role_definition_name == "Network Contributor"
    error_message = "The cluster identity's network role is scoped to the node subnet."
  }
}

run "one_node_in_the_trust_subnet" {
  command = plan

  assert {
    condition     = azurerm_kubernetes_cluster.this.default_node_pool[0].node_count == 1 && azurerm_kubernetes_cluster.this.default_node_pool[0].vm_size == "Standard_D4s_v5"
    error_message = "One Standard_D4s_v5 node: the 4-vCPU trial quota."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.sku_tier == "Free"
    error_message = "The control plane's free tier."
  }
}

run "needs_the_operator_ip" {
  command = plan

  variables {
    operator_ip = ""
  }

  expect_failures = [var.operator_ip]
}
