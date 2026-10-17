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

variable "subscription_id" {
  description = "The Azure subscription to deploy into, as a GUID. Never the CLI default."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "uksouth"
}

variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
  default     = "flipaz"
}

variable "existing_subnet_id" {
  description = "A subnet the site supplies, whose route table sends outbound traffic through the site's firewall (AKS userDefinedRouting refuses a subnet without one). Empty creates a network that leaves through its own NAT gateway."
  type        = string
  default     = ""
}

variable "node_egress_ips" {
  description = "With existing_subnet_id: the public IPs the site's egress gives the nodes (its firewall's). The API server admits them, or the nodes cannot reach it. Ignored on a created network, whose NAT gateway IP is admitted instead."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for ip in var.node_egress_ips : can(cidrhost("${ip}/32", 0))])
    error_message = "node_egress_ips must be IPv4 addresses."
  }
  validation {
    condition     = var.existing_subnet_id == "" || length(var.node_egress_ips) > 0
    error_message = "A site subnet (existing_subnet_id) needs node_egress_ips, the site's egress IPs: the API server must admit them or the nodes never join."
  }
}

variable "operator_ip" {
  description = "The one public IP the API server admits (the Makefile looks it up)."
  type        = string

  validation {
    condition     = can(cidrhost("${var.operator_ip}/32", 0))
    error_message = "operator_ip must be an IPv4 address: the API server admits only it. The Makefile looks it up from api.ipify.org: if that failed, pass OPERATOR_IP=<your public IPv4>."
  }
}

variable "node_size" {
  description = "Node VM size; the default fits the 4-vCPU trial quota."
  type        = string
  default     = "Standard_D4s_v5"
}

variable "kubernetes_version" {
  description = "Kubernetes minor version; empty takes the region's default."
  type        = string
  default     = ""
}
