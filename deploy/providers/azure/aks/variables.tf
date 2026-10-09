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
  description = "Azure region; the cluster and the bootstrap's outbound IP must share it."
  type        = string
  default     = "uksouth"
}

variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
  default     = "flipaz"
}

variable "egress_public_ip_id" {
  description = "The bootstrap's static public IP: the cluster load balancer's only outbound address."
  type        = string
}

variable "operator_ip" {
  description = "The one public IP the API server admits (the Makefile looks it up)."
  type        = string

  validation {
    condition     = can(cidrhost("${var.operator_ip}/32", 0))
    error_message = "operator_ip must be an IPv4 address: the API server admits only it."
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
