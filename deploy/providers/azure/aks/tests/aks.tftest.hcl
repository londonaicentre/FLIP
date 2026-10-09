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
  subscription_id     = "00000000-0000-0000-0000-000000000000"
  egress_public_ip_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/flipaz-bootstrap-rg/providers/Microsoft.Network/publicIPAddresses/flipaz-egress-ip"
  operator_ip         = "203.0.113.7"
}

run "api_server_admits_only_the_operator" {
  command = plan

  assert {
    condition     = azurerm_kubernetes_cluster.this.api_server_access_profile[0].authorized_ip_ranges == toset(["203.0.113.7/32"])
    error_message = "Only the operator's IP may reach the API server."
  }
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
}

run "network_policies_are_enforced" {
  command = plan

  assert {
    condition     = azurerm_kubernetes_cluster.this.network_profile[0].network_policy == "cilium" && azurerm_kubernetes_cluster.this.network_profile[0].network_data_plane == "cilium"
    error_message = "The chart's NetworkPolicies must be enforced (Cilium)."
  }
}

run "egress_leaves_from_the_bootstrap_ip" {
  command = plan

  assert {
    condition     = azurerm_kubernetes_cluster.this.network_profile[0].outbound_type == "loadBalancer"
    error_message = "Egress goes through the cluster load balancer."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.network_profile[0].load_balancer_profile[0].outbound_ip_address_ids == toset([var.egress_public_ip_id])
    error_message = "The load balancer's only outbound IP is the bootstrap's static one."
  }
  assert {
    condition     = azurerm_role_assignment.cluster_uses_egress_ip.role_definition_name == "Network Contributor" && azurerm_role_assignment.cluster_uses_egress_ip.scope == var.egress_public_ip_id
    error_message = "The cluster identity may use the bootstrap IP and nothing broader."
  }
  assert {
    condition     = module.network.attach_public_ip_to_nic == false
    error_message = "The network module puts no IP on a NIC (egress_mode none): the load balancer carries egress."
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
